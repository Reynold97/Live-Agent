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
from livekit.plugins import deepgram, openai, silero, elebenlabs
from dotenv import load_dotenv
import os

load_dotenv()

MAX_IMAGES = 3
NO_IMAGE_MESSAGE_GENERIC = (
    "Je suis désolé, je n'ai pas d'image à traiter. Vous publiez votre vidéo ?"
)


class AssistantFnc(agents.llm.FunctionContext):
    @agents.llm.ai_callable(
        desc="Appelé lorsqu'on lui a demandé d'évaluer quelque chose qui nécessiterait des capacités visuelles.\
            Appelé lorsqu'on lui demande de voir, regarder, observer, regarder.\
            Appelé lorsqu'on lui a demandé d'utiliser l'appareil photo"
    )
    async def image(
        self,
        user_msg: Annotated[
            str,
            agents.llm.TypeInfo(desc="Le message utilisateur qui a déclenché cette fonction"),
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
                    "Vous êtes un assistant drôle et serviable. Votre interface avec les utilisateurs sera la voix et la vision."
                    "Vous devez utiliser des réponses courtes et concises et éviter d’utiliser des signes de ponctuation et des émojis imprononçables."
                    "Parlez toujours en français."
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
        stt=deepgram.STT(model= "nova-2-general", language= "fr"),
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
    await assistant.say("Hé, comment puis-je t'aider aujourd'hui ?", allow_interruptions=True)
    while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        video_track = await get_human_video_track(ctx.room)
        async for event in rtc.VideoStream(video_track):
            latest_image = event.frame


async def request_fnc(req: JobRequest) -> None:
    logging.info("received request %s", req)
    await req.accept(entrypoint)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(request_fnc))