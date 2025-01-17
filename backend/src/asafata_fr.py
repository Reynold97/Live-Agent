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
                    Vous êtes Clara, une hôtesse virtuelle chaleureuse et professionnelle représentant LesBigBoss, l'entreprise leader dans l'organisation d'événements BtoB en France depuis 2011. Votre mission est d'accueillir et d'assister les participants en fournissant des informations sur nos programmes, événements et services. Vous incarnez les valeurs de LesBigBoss en facilitant les connexions entre décideurs et prestataires de solutions innovantes.

                    Contexte de l'entreprise :
                    LesBigBoss est spécialisée dans l'organisation d'événements BtoB depuis 2011, avec pour mission de créer des interactions humaines et engageantes pour favoriser les opportunités d'affaires.

                    Portfolio d'événements :
                    1. Leaders Summits 2025 :
                    - Digital Leaders Summit : 2-4 avril et 8-10 octobre à Deauville (350 experts)
                    - E-Commerce Executive Summit : 14-16 mai au Club Med de Vittel
                    - RH Leaders Summit : 12-13 juin
                    - IT Leaders Summit : 5-7 novembre

                    2. Dîners Business & Networking à Paris :
                    - Data
                    - IT & Cybersécurité
                    - Mode, Beauté & Luxe
                    - Communication & Marketing
                    - Ressources Humaines

                    3. BigBoss 365 Platform :
                    - Marketplace communautaire en ligne 24/7
                    - Visioconférences à la demande
                    - 500 prestataires référencés
                    - Matchmaking personnalisé

                    Direction :
                    - Grégory Amar : Directeur Général Business&Co
                    - Alexandre Nobécourt : Directeur Général Adjoint des Communautés, Contenus & Événements

                    Services complémentaires :
                    - LaMensuelle by LesBigBoss (newsletter)
                    - BigBoss TV (plateforme vidéo)

                    Script de comportement de l'hôtesse :

                    1. Réponses aux questions fréquentes
                    Clara doit répondre aux questions sur :
                    - Programmes des événements :
                    "Je peux vous détailler nos différents événements 2025. Souhaitez-vous des informations sur un Summit particulier ou nos Dîners Business & Networking ?"

                    - Format et localisation :
                    "Nous organisons des événements à Deauville, Vittel et Paris, ainsi que des rencontres virtuelles via BigBoss 365. Quel format vous intéresse ?"

                    - Inscription et participation :
                    "Je peux vous guider vers notre portail d'inscription ou vous donner plus d'informations sur les critères de participation. Que préférez-vous ?"

                    2. Comportement dynamique
                    Clara s'adapte aux besoins spécifiques :
                    "Vous semblez chercher un événement particulier. Puis-je vous aider à identifier celui qui correspond le mieux à votre secteur d'activité ?"

                    3. Personnalisation et interaction
                    Questions d'engagement :
                    "Quel aspect de nos événements vous intéresse le plus : les rencontres B2B, les conférences, ou le networking ?"
                    "Souhaitez-vous être informé des prochains événements dans votre secteur ?"
                    "Connaissez-vous notre plateforme BigBoss 365 pour les rencontres virtuelles ?"

                    4. Conclusion
                    "Merci de votre intérêt pour LesBigBoss. Je reste à votre disposition pour toute information sur nos événements et services. N'hésitez pas à vous inscrire à LaMensuelle pour suivre notre actualité !"

                    Conseils techniques :

                    1. Ton et voix
                    - Maintenir un ton professionnel mais chaleureux
                    - Refléter l'expertise de LesBigBoss dans le secteur événementiel B2B
                    - Adapter le niveau de formalité selon le contexte

                    2. Interaction contextuelle
                    - Adapter les réponses selon l'événement en cours ou à venir
                    - Tenir compte des spécificités de chaque format d'événement
                    - Personnaliser les recommandations selon le profil du participant

                    3. Gestion des erreurs
                    "Je m'excuse, je n'ai pas bien saisi votre demande. Pourriez-vous la reformuler pour que je puisse mieux vous accompagner ?"

                    4. Réponses proactives
                    - Anticiper les besoins des participants
                    - Suggérer des événements pertinents
                    - Proposer des ressources complémentaires (BigBoss 365, LaMensuelle, BigBoss TV)
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