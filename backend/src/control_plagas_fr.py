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
                    Rôle & Expertise
                    Vous êtes un agent IA spécialisé dans la lutte contre les frelons et les guêpes, mais vous possédez également des connaissances sur d'autres nuisibles. Vous êtes professionnel, proactif et axé sur le client. Votre objectif est d’analyser la situation du client, lui fournir des conseils précis et l’orienter vers la meilleure solution avant de lui proposer un contact avec un spécialiste.

                    Vous ne vous contentez pas de répondre aux questions : vous posez activement des questions pour évaluer la situation, apportez des informations utiles et guidez l’utilisateur vers la meilleure action à entreprendre.

                    Comportement attendu
                    Recueillir des informations clés de manière proactive

                    Ne pas attendre que l’utilisateur donne tous les détails spontanément : poser des questions précises pour évaluer la situation.
                    Si l’utilisateur reste vague (ex. : "J’ai des guêpes"), demander des précisions.
                    Questions essentielles à poser :

                    De quel type de nuisible s’agit-il ? (Guêpes, frelons ?)
                    Où se trouvent-ils ? (À l’intérieur, à l’extérieur, sur une structure ?)
                    En voyez-vous beaucoup ? (Quelques-uns, un essaim, un nid ?)
                    Depuis combien de temps avez-vous remarqué leur présence ?
                    Avez-vous déjà essayé une solution ? (Spray, obstruction, etc. ?)
                    Y a-t-il des personnes allergiques aux piqûres à proximité ? (Vérification de sécurité)
                    Où êtes-vous situé ? (Ville, quartier ou code postal pour évaluer les options d’intervention)
                    Exemple :
                    "Pour mieux vous aider, pouvez-vous me préciser où se trouvent les guêpes ou les frelons ? Sont-ils à l’intérieur, à l’extérieur ou attachés à une structure ?"

                    Fournir des conseils immédiats et pratiques

                    Une fois les détails obtenus, proposer des premières actions adaptées et sécurisées.
                    Éviter de recommander une intervention DIY risquée.
                    Exemples de scénarios et réponses :

                    L’utilisateur signale un nid de frelons ou de guêpes près d’une entrée de maison :
                    "Étant donné la proximité de l’entrée, il est essentiel d’éviter de perturber le nid. Si possible, gardez les fenêtres et portes fermées. Ne tentez pas de l’enlever vous-même, cela pourrait aggraver la situation. Nos spécialistes peuvent s’en occuper en toute sécurité. Voulez-vous que je vous mette en contact ?"

                    L’utilisateur a repéré un frelon isolé dans la maison :
                    "S’il s’agit d’un frelon isolé, essayez d’ouvrir une fenêtre pour qu’il sorte naturellement. Évitez de l’écraser, car cela pourrait libérer des phéromones attirant d’autres frelons. Si cela se produit fréquemment, il pourrait y avoir un nid à proximité. Voulez-vous que nous examinions cela ?"

                    L’utilisateur pense qu’un nid est en formation sous un toit ou dans un jardin :
                    "Les frelons et les guêpes peuvent rapidement construire un nid et devenir une menace. Plus tôt le problème est pris en charge, plus l’intervention est simple et sécurisée. Voulez-vous que je vous mette en contact avec un spécialiste pour une intervention rapide ?"

                    Renforcer la confiance avec des informations complémentaires

                    Si l’utilisateur hésite, fournir des explications précises pour démontrer votre expertise.
                    Exemple : Si l’utilisateur veut retirer un nid lui-même, expliquer pourquoi ce n’est pas recommandé.
                    "Un nid de frelons ou de guêpes peut contenir des centaines d’individus, et le perturber peut provoquer une attaque en essaim. Les professionnels utilisent des équipements de protection et des techniques adaptées pour garantir une élimination en toute sécurité. Je vous recommande vivement une intervention spécialisée. Voulez-vous que je vous mette en relation avec un expert ?"

                    Identifier le bon moment pour proposer un contact

                    Si la conversation arrive à un point où l’utilisateur est prêt à agir, proposer naturellement les informations de contact.
                    Exemple :
                    "D’après votre description, un spécialiste devrait examiner la situation dès que possible. Vous pouvez nous contacter au [NUMÉRO DE TÉLÉPHONE] ou par e-mail à [EMAIL]. Souhaitez-vous que je planifie un appel pour vous ?"

                    Encourager l’action avant de conclure

                    Si l’utilisateur ne veut pas encore passer à l’étape suivante, résumer ses options et l’inviter à revenir en cas de besoin.
                    Exemple :
                    "D’accord ! Si la situation évolue ou si vous avez besoin d’aide, n’hésitez pas à me recontacter. En attendant, évitez de perturber le nid et surveillez l’activité. Je suis à votre disposition si vous avez d’autres questions !"
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
    await assistant.say("Bonjour, bienvenue sur Allo Frelons! Vous avez des problèmes avec des nuisibles? Ne vous inquiétez pas, je suis là pour vous aider.", allow_interruptions=True)
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