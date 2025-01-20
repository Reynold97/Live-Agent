import asyncio
import copy
import logging
from collections import deque
from typing import Annotated, List

from livekit import agents, rtc
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli, tokenize, tts
from livekit.agents.llm import (
    ChatContext,
    ChatMessage,
    ChatRole,
)
from livekit.agents.voice_assistant import AssistantContext, VoiceAssistant
from livekit.plugins import deepgram, openai, silero#, elebenlabs
from dotenv import load_dotenv
import os
import logging
from logging_config import setup_logging

load_dotenv()

MAX_IMAGES = 3
NO_IMAGE_MESSAGE_GENERIC = (
    "I'm sorry, I don't have an image to process. Are you publishing your video?"
)


class AssistantFnc(agents.llm.FunctionContext):
    @agents.llm.ai_callable(
        desc="Called when asked to evaluate something that would require vision capabilities.\
            Called when asked to see, watch, observe, look.\
            Called when asked to use the camera"
    )
    async def image(
        self,
        user_msg: Annotated[
            str,
            agents.llm.TypeInfo(desc="The user message that triggered this function"),
        ],
    ):
        ctx = AssistantContext.get_current()
        ctx.store_metadata("user_msg", user_msg)


async def get_human_video_track(room: rtc.Room):
    track_future = asyncio.Future[rtc.RemoteVideoTrack]()

    def on_sub(track: rtc.Track, *_):
        if isinstance(track, rtc.RemoteVideoTrack):
            track_future.set_result(track)

    room.on("track_subscribed", on_sub)

    remote_video_tracks: List[rtc.RemoteVideoTrack] = []
    for _, p in room.participants.items():
        for _, t_pub in p.tracks.items():
            if t_pub.track is not None and isinstance(
                t_pub.track, rtc.RemoteVideoTrack
            ):
                remote_video_tracks.append(t_pub.track)

    if len(remote_video_tracks) > 0:
        track_future.set_result(remote_video_tracks[0])

    video_track = await track_future
    room.off("track_subscribed", on_sub)
    return video_track


async def entrypoint(ctx: JobContext):
    sip = ctx.room.name.startswith("sip")
    initial_ctx = ChatContext(
        messages=[
            ChatMessage(
                role=ChatRole.SYSTEM,
                text=(
                    """
                    You are a witty and sarcastic poker companion with a heart of gold. Your primary roles are:

                    PERSONALITY:
                    - Use playful sarcasm and witty observations, but never cross into mean-spirited territory
                    - Maintain a "seen it all" veteran poker player vibe
                    - Mix poker terminology with humorous metaphors
                    - Show genuine empathy beneath the playful exterior

                    INTERACTION STYLE:
                    - Start with light roasting about the player's situation
                    - Use poker-specific humor (e.g., "Did you really call with Queen-high? Even a magic 8-ball would've folded that!")
                    - Gradually transition from humor to constructive support
                    - End with encouragement and a dash of humor

                    RESPONSE STRUCTURE:
                    1. Initial Reaction: A witty observation about the situation
                    2. Playful Roast: Good-natured teasing about specific plays
                    3. Empathy Break: Brief acknowledgment of the frustration
                    4. Constructive Twist: Turn the situation into a learning opportunity
                    5. Encouraging Close: Mix hope with humor

                    EXAMPLE RESPONSES:

                    After a bad beat:
                    "Ah, the classic 'they called with 7-2 and hit a full house' story. Did you also get struck by lightning while walking under a ladder on your way home? But seriously, even a broken clock is right twice a day - which is still more often than that play will work out for them. Let's channel that rage into your next session..."

                    When someone goes all-in pre-flop with weak hands:
                    "Going all-in with Jack-four suited? I see we're using the 'any two cards can win' strategy. That's like jumping out of a plane and hoping to land on a mattress. Bold strategy, Cotton! Next time, maybe we try something crazy - like actually waiting for good cards?"

                    After multiple losing sessions:
                    "Three busted sessions in a row? You're either the unluckiest player alive or making decisions that would make a magic 8-ball look like a poker genius. But hey, at least you're consistent! Let's turn this comedy of errors into a comeback story..."

                    When someone keeps calling with drawing hands:
                    "Calling off your stack with a gutshot straight draw? I haven't seen someone chase something this hopeless since my ex tried to become a professional mime. But look on the bright side - you're giving everyone else at the table a masterclass in bankroll donations!"

                    After tilting and making bad decisions:
                    "So you're telling me you went on tilt and played every hand for an hour? Well, that's one way to make sure the dealer doesn't get bored! I haven't seen someone throw chips around like that since a waiter dropped a plate at a Greek restaurant. Time for some deep breaths and a reality check, champ..."

                    When someone overplays a medium strength hand:
                    "Shoving with middle pair? That's like bringing a pool noodle to a sword fight and acting surprised when you get cut. I admire your optimism though - maybe next time we try this revolutionary technique called 'folding'?"

                    After a failed bluff:
                    "That bluff was so transparent, I could've used it as a window! Even Helen Keller would've seen through that one. But hey, points for creativity - I especially loved the part where you convinced yourself the table was buying it..."

                    During a card dead streak:
                    "Haven't seen a playable hand in two hours? Welcome to the 'Seven-Deuce Support Group'! We meet every time someone thinks the deck is personally plotting against them. Spoiler alert: the cards aren't mad at you, they're just in a committed relationship with everyone else at the table!"
                    
                    """
                ),
            )
        ]
    )

    gpt = openai.LLM(
        model="gpt-4o",
    )
    openai_tts = tts.StreamAdapter(
        tts=openai.TTS(voice="alloy"),
        sentence_tokenizer=tokenize.basic.SentenceTokenizer(),
    )
    latest_image: rtc.VideoFrame | None = None
    img_msg_queue: deque[agents.llm.ChatMessage] = deque()
    assistant = VoiceAssistant(
        vad=silero.VAD(),
        stt=deepgram.STT(model= "nova-2-general"),
        llm=gpt,
        #tts=elevenlabs.TTS(encoding="pcm_44100"),
        tts=openai_tts,
        fnc_ctx=None if sip else AssistantFnc(),
        chat_ctx=initial_ctx,
    )

    chat = rtc.ChatManager(ctx.room)

    async def _answer_from_text(text: str):
        chat_ctx = copy.deepcopy(assistant.chat_context)
        chat_ctx.messages.append(ChatMessage(role=ChatRole.USER, text=text))

        stream = await gpt.chat(chat_ctx)
        await assistant.say(stream)

    @chat.on("message_received")
    def on_chat_received(msg: rtc.ChatMessage):
        if not msg.message:
            return

        asyncio.create_task(_answer_from_text(msg.message))

    async def respond_to_image(user_msg: str):
        nonlocal latest_image, img_msg_queue, initial_ctx
        if not latest_image:
            await assistant.say(NO_IMAGE_MESSAGE_GENERIC)
            return

        initial_ctx.messages.append(
            agents.llm.ChatMessage(
                role=agents.llm.ChatRole.USER,
                text=user_msg,
                images=[agents.llm.ChatImage(image=latest_image)],
            )
        )
        img_msg_queue.append(initial_ctx.messages[-1])
        if len(img_msg_queue) >= MAX_IMAGES:
            msg = img_msg_queue.popleft()
            msg.images = []

        stream = await gpt.chat(initial_ctx)
        await assistant.say(stream, allow_interruptions=True)

    @assistant.on("function_calls_finished")
    def _function_calls_done(ctx: AssistantContext):
        user_msg = ctx.get_metadata("user_msg")
        if not user_msg:
            return
        asyncio.ensure_future(respond_to_image(user_msg))

    assistant.start(ctx.room)

    await asyncio.sleep(0.5)
    await assistant.say("Ah, pulled up a chair at the therapy table, have we? What's the damage?", allow_interruptions=True)
    while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        video_track = await get_human_video_track(ctx.room)
        async for event in rtc.VideoStream(video_track):
            latest_image = event.frame


async def request_fnc(req: JobRequest) -> None:
    logging.info("received request %s", req)
    await req.accept(entrypoint)


if __name__ == "__main__":
    setup_logging()
    logging.info("Agent2 started")
    cli.run_app(WorkerOptions(request_fnc))