#!/usr/bin/env python
import math
import sys
from os import name
from pathlib import Path
from subprocess import Popen
from typing import Dict, NoReturn

import ffmpeg
from prawcore import ResponseException

from reddit.subreddit import get_subreddit_threads
from utils import settings
from utils.cleanup import cleanup
from utils.console import print_markdown, print_step, print_substep
from utils.ffmpeg_install import ffmpeg_install
from utils.id import extract_id
from utils.version import checkversion
from video_creation.background import (
    chop_background,
    download_background_audio,
    download_background_video,
    get_background_config,
)
from video_creation.final_video import make_final_video
from video_creation.screenshot_downloader import get_screenshots_of_reddit_posts
from video_creation.voices import save_text_to_mp3

__VERSION__ = "3.4.0"

MAX_VIDEO_SECONDS = 90
END_SCREEN_SECONDS = 5
MAX_CONTENT_SECONDS = MAX_VIDEO_SECONDS - END_SCREEN_SECONDS
PART_NUMBER_WORDS = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
}

print(
    """
██████╗ ███████╗██████╗ ██████╗ ██╗████████╗    ██╗   ██╗██╗██████╗ ███████╗ ██████╗     ███╗   ███╗ █████╗ ██╗  ██╗███████╗██████╗
██╔══██╗██╔════╝██╔══██╗██╔══██╗██║╚══██╔══╝    ██║   ██║██║██╔══██╗██╔════╝██╔═══██╗    ████╗ ████║██╔══██╗██║ ██╔╝██╔════╝██╔══██╗
██████╔╝█████╗  ██║  ██║██║  ██║██║   ██║       ██║   ██║██║██║  ██║█████╗  ██║   ██║    ██╔████╔██║███████║█████╔╝ █████╗  ██████╔╝
██╔══██╗██╔══╝  ██║  ██║██║  ██║██║   ██║       ╚██╗ ██╔╝██║██║  ██║██╔══╝  ██║   ██║    ██║╚██╔╝██║██╔══██║██╔═██╗ ██╔══╝  ██╔══██╗
██║  ██║███████╗██████╔╝██████╔╝██║   ██║        ╚████╔╝ ██║██████╔╝███████╗╚██████╔╝    ██║ ╚═╝ ██║██║  ██║██║  ██╗███████╗██║  ██║
╚═╝  ╚═╝╚══════╝╚═════╝ ╚═════╝ ╚═╝   ╚═╝         ╚═══╝  ╚═╝╚═════╝ ╚══════╝ ╚═════╝     ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
"""
)
print_markdown(
    "### Thanks for using this tool! Feel free to contribute to this project on GitHub! If you have any questions, feel free to join my Discord server or submit a GitHub issue. You can find solutions to many common problems in the documentation: https://reddit-video-maker-bot.netlify.app/"
)
checkversion(__VERSION__)

reddit_id: str
reddit_object: Dict[str, str | list]


def main(POST_ID=None) -> None:
    global reddit_id, reddit_object
    reddit_object = get_subreddit_threads(POST_ID)
    reddit_id = extract_id(reddit_object)
    print_substep(f"Thread ID is {reddit_id}", style="bold blue")
    length, number_of_comments = save_text_to_mp3(reddit_object)
    length = math.ceil(length)
    get_screenshots_of_reddit_posts(reddit_object, number_of_comments)
    bg_config = {
        "video": get_background_config("video"),
        "audio": get_background_config("audio"),
    }
    download_background_video(bg_config["video"])
    download_background_audio(bg_config["audio"])
    chop_background(bg_config, length, reddit_object)
    if length <= MAX_VIDEO_SECONDS:
        make_final_video(number_of_comments, length, reddit_object, bg_config)
        return

    if settings.config["settings"]["storymode"] and settings.config["settings"]["storymodemethod"] == 0:
        print_substep(
            "Storymode method 0 does not support automatic splitting; rendering as a single video."
        )
        make_final_video(number_of_comments, length, reddit_object, bg_config)
        return

    title_duration = float(
        ffmpeg.probe(f"assets/temp/{reddit_id}/mp3/title.mp3")["format"]["duration"]
    )
    if settings.config["settings"]["storymode"] and settings.config["settings"]["storymodemethod"] == 1:
        clip_paths = [
            f"assets/temp/{reddit_id}/mp3/postaudio-{i}.mp3" for i in range(number_of_comments)
        ]
    else:
        clip_paths = [f"assets/temp/{reddit_id}/mp3/{i}.mp3" for i in range(number_of_comments)]

    clip_durations = [float(ffmpeg.probe(path)["format"]["duration"]) for path in clip_paths]
    max_segment_seconds = MAX_CONTENT_SECONDS - title_duration
    if max_segment_seconds <= 0:
        print_substep(
            "Title audio is too long to fit within the 90 second limit; rendering as a single video."
        )
        make_final_video(number_of_comments, length, reddit_object, bg_config)
        return

    segments = split_video_segments(clip_durations, max_segment_seconds)
    if len(segments) <= 1:
        make_final_video(number_of_comments, length, reddit_object, bg_config)
        return

    total_parts = len(segments)
    for part_number, (start_index, end_index, segment_length) in enumerate(segments, start=1):
        part_length = math.ceil(title_duration + segment_length)
        end_screen_seconds = END_SCREEN_SECONDS if part_number < total_parts else 0
        end_screen_text = (
            f"Part {format_part_number(part_number + 1)} Coming Soon"
            if part_number < total_parts
            else ""
        )
        make_final_video(
            number_of_comments,
            part_length,
            reddit_object,
            bg_config,
            start_index=start_index,
            end_index=end_index,
            part_number=part_number,
            total_parts=total_parts,
            end_screen_seconds=end_screen_seconds,
            end_screen_text=end_screen_text,
        )


def split_video_segments(durations, max_segment_seconds):
    segments = []
    start_index = 0
    current_length = 0.0
    for index, duration in enumerate(durations):
        if current_length + duration > max_segment_seconds and current_length > 0:
            segments.append((start_index, index, current_length))
            start_index = index
            current_length = 0.0
        current_length += duration

    if start_index < len(durations):
        segments.append((start_index, len(durations), current_length))
    return segments


def format_part_number(part_number):
    return PART_NUMBER_WORDS.get(part_number, str(part_number))


def run_many(times) -> None:
    for x in range(1, times + 1):
        print_step(
            f'on the {x}{("th", "st", "nd", "rd", "th", "th", "th", "th", "th", "th")[x % 10]} iteration of {times}'
        )
        main()
        Popen("cls" if name == "nt" else "clear", shell=True).wait()


def shutdown() -> NoReturn:
    if "reddit_id" in globals():
        print_markdown("## Clearing temp files")
        cleanup(reddit_id)

    print("Exiting...")
    sys.exit()


if __name__ == "__main__":
    if sys.version_info.major != 3 or sys.version_info.minor not in [10, 11, 12]:
        print(
            "Hey! Congratulations, you've made it so far (which is pretty rare with no Python 3.10). Unfortunately, this program only works on Python 3.10. Please install Python 3.10 and try again."
        )
        sys.exit()
    ffmpeg_install()
    directory = Path().absolute()
    config = settings.check_toml(
        f"{directory}/utils/.config.template.toml", f"{directory}/config.toml"
    )
    config is False and sys.exit()

    if (
        not settings.config["settings"]["tts"]["tiktok_sessionid"]
        or settings.config["settings"]["tts"]["tiktok_sessionid"] == ""
    ) and config["settings"]["tts"]["voice_choice"] == "tiktok":
        print_substep(
            "TikTok voice requires a sessionid! Check our documentation on how to obtain one.",
            "bold red",
        )
        sys.exit()
    try:
        if config["reddit"]["thread"]["post_id"]:
            for index, post_id in enumerate(config["reddit"]["thread"]["post_id"].split("+")):
                index += 1
                print_step(
                    f'on the {index}{("st" if index % 10 == 1 else ("nd" if index % 10 == 2 else ("rd" if index % 10 == 3 else "th")))} post of {len(config["reddit"]["thread"]["post_id"].split("+"))}'
                )
                main(post_id)
                Popen("cls" if name == "nt" else "clear", shell=True).wait()
        elif config["settings"]["times_to_run"]:
            run_many(config["settings"]["times_to_run"])
        else:
            main()
    except KeyboardInterrupt:
        shutdown()
    except ResponseException:
        print_markdown("## Invalid credentials")
        print_markdown("Please check your credentials in the config.toml file")
        shutdown()
    except Exception as err:
        config["settings"]["tts"]["tiktok_sessionid"] = "REDACTED"
        config["settings"]["tts"]["elevenlabs_api_key"] = "REDACTED"
        config["settings"]["tts"]["openai_api_key"] = "REDACTED"
        print_step(
            f"Sorry, something went wrong with this version! Try again, and feel free to report this issue at GitHub or the Discord community.\n"
            f"Version: {__VERSION__} \n"
            f"Error: {err} \n"
            f'Config: {config["settings"]}'
        )
        raise err
