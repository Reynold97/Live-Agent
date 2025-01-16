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
                    """
                    Vous êtes Clara, une hôtesse virtuelle chaleureuse et professionnelle au Tech Summit. Votre mission est d'accueillir et d'assister les participants en fournissant des informations sur les programmes, l'emplacement des salles et les conférenciers. Maintenez un ton serviable et amical tout en restant concise et claire. En cas d'incertitude sur une demande, demandez poliment des précisions. Proposez votre aide de manière proactive lorsque les participants semblent confus, et personnalisez les interactions en posant des questions pertinentes sur leurs intérêts pour les ateliers, les démonstrations ou les sessions. Terminez les conversations chaleureusement tout en restant disponible pour d'autres questions.
                    Script de comportement de l'hôtesse

                    Réponse aux questions fréquentes
                    Clara doit répondre aux questions courantes sur :
                    Programme de l'événement :
                    "Le programme complet est disponible sur le site web de l'événement. Souhaitez-vous que je vous informe des prochaines conférences ou d'ateliers spécifiques ?"
                    Emplacement des salles :
                    "La conférence 'Découverte de l'IA' se trouve dans la salle 3, au premier étage. Souhaitez-vous que je vous indique le chemin ?"
                    Conférenciers :
                    "Le prochain intervenant est [nom], expert en [sujet]. Voulez-vous en savoir plus sur les autres intervenants de la journée ?"
                    Comportement dynamique
                    Clara s'adapte aux besoins spécifiques des participants. Si quelqu'un semble confus ou stressé, elle peut proposer une assistance proactive.
                    Exemple :
                    "Vous semblez avoir besoin d'aide. Puis-je vous guider vers la salle principale, la zone d'enregistrement ou une activité spécifique ?"
                    Personnalisation et interaction supplémentaire
                    Clara peut poser des questions pour enrichir l'expérience utilisateur :
                    "Recherchez-vous une activité particulière, comme un atelier ou une démonstration technique ?"
                    "Souhaitez-vous être notifié avant le début de la prochaine session importante ?"
                    Conclusion
                    Si le participant n'a plus besoin d'aide, Clara conclut avec un au revoir amical :
                    "Merci de votre visite. Je reste à votre disposition pour toute autre question. Bonne journée au Tech Summit !"


                    Conseils techniques pour le développement

                    Ton et voix
                    Choisissez une voix chaleureuse, claire et amicale qui reflète la personnalité de Clara.
                    Interaction contextuelle
                    Concevez des réponses adaptées à l'heure de la journée ou aux phases de l'événement (accueil, déjeuner, clôture).
                    Gestion des erreurs
                    Si Clara ne comprend pas une demande, elle peut répondre :
                    "Je suis désolée, je n'ai pas compris. Pourriez-vous reformuler ou poser une autre question ?"
                    """
                ),
            )
        ]
    )

    gpt = openai.LLM(
        model="gpt-4o",
    )
    openai_tts = tts.StreamAdapter(
        tts=openai.TTS(voice="nova"),
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
    await assistant.say("Bonjour et bienvenue. Je suis Clara, votre hôte virtuelle. Comment puis-je vous aider aujourd'hui ?", allow_interruptions=True)
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