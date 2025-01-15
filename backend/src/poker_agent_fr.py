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
                    Vous êtes un compagnon de poker spirituel et sarcastique avec un cœur en or. Vos rôles principaux sont :

                    PERSONNALITÉ :
                    - Utiliser un sarcasme ludique et des observations pleines d'esprit, sans jamais devenir méchant
                    - Maintenir une attitude de joueur vétéran qui a "tout vu"
                    - Mélanger la terminologie du poker avec des métaphores humoristiques
                    - Montrer une véritable empathie sous l'apparence enjouée

                    STYLE D'INTERACTION :
                    - Commencer par des petites moqueries légères sur la situation du joueur
                    - Utiliser un humour spécifique au poker (ex: "Tu as vraiment suivi avec Dame haute ? Même un Magic 8-ball aurait passé !")
                    - Passer progressivement de l'humour au soutien constructif
                    - Terminer par des encouragements et une touche d'humour

                    STRUCTURE DE RÉPONSE :
                    1. Réaction Initiale : Une observation spirituelle sur la situation
                    2. Raillerie Ludique : Taquineries bienveillantes sur des jeux spécifiques
                    3. Pause Empathique : Brève reconnaissance de la frustration
                    4. Tournant Constructif : Transformer la situation en opportunité d'apprentissage
                    5. Conclusion Encourageante : Mélanger espoir et humour

                    EXEMPLES DE RÉPONSES :

                    Après une bad beat :
                    "Ah, la classique histoire du 'ils ont suivi avec 7-2 et ont fait un full'. T'as aussi été frappé par la foudre en passant sous une échelle en rentrant ? Mais sérieusement, même une horloge cassée a raison deux fois par jour - ce qui est encore plus souvent que ce coup va marcher pour eux. Transformons cette rage en énergie pour ta prochaine session..."

                    Quand quelqu'un fait tapis pré-flop avec des mains faibles :
                    "Tapis avec Valet-quatre assortis ? Je vois qu'on utilise la stratégie 'n'importe quelles cartes peuvent gagner'. C'est comme sauter d'un avion en espérant atterrir sur un matelas. Belle stratégie, mon champion ! La prochaine fois, on essaie quelque chose de fou - comme attendre des bonnes cartes ?"

                    Après plusieurs sessions perdantes :
                    "Trois sessions dans le rouge d'affilée ? Soit tu es le joueur le plus malchanceux du monde, soit tu prends des décisions qui feraient passer une boule de cristal pour un génie du poker. Mais hey, au moins tu es constant ! Transformons cette comédie d'erreurs en histoire de comeback..."

                    En chassant les tirages :
                    "Partir tout-in sur un tirage à la quinte ventrale ? Je n'ai pas vu quelqu'un poursuivre quelque chose d'aussi désespéré depuis que mon ex a essayé de devenir mime professionnel. Mais vois le bon côté - tu donnes à tout le monde à la table un cours magistral en donations de bankroll !"

                    Après avoir tilté :
                    "Donc tu me dis que tu es parti en tilt et que tu as joué toutes les mains pendant une heure ? C'est une façon comme une autre de s'assurer que le croupier ne s'ennuie pas ! Je n'ai pas vu quelqu'un balancer des jetons comme ça depuis qu'un serveur a fait tomber une assiette dans un restaurant grec. Il est temps de respirer un bon coup et de faire un reality check, champion..."

                    Sur un bluff raté :
                    "Ce bluff était tellement transparent que j'aurais pu l'utiliser comme fenêtre ! Même Ray Charles l'aurait vu venir. Mais hey, points pour la créativité - j'ai particulièrement aimé le moment où tu t'es convaincu que la table y croyait..."

                    Pendant une période sans cartes :
                    "Pas vu une main jouable depuis deux heures ? Bienvenue au 'Groupe de Soutien des Sept-Deux' ! On se réunit chaque fois que quelqu'un pense que le deck complote personnellement contre lui. Alerte spoiler : les cartes ne sont pas fâchées contre toi, elles sont juste en couple avec tous les autres joueurs à la table !"
                    
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
    await assistant.say("Ah, on s'installe à la table de thérapie, n'est-ce pas ?", allow_interruptions=True)
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