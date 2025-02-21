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
    "Lo siento, no tengo una imagen para procesar. ¿Estás publicando tu video?"
)


class AssistantFnc(agents.llm.FunctionContext):
    @agents.llm.ai_callable(
        desc="""Llamado cuando se le pide evaluar algo que requeriría capacidades de visión.
                Llamado cuando se le pide ver, mirar, observar, contemplar.
                Llamado cuando se le pide usar la cámara."""
    )
    async def image(
        self,
        user_msg: Annotated[
            str,
            agents.llm.TypeInfo(desc="El mensaje del usuario que activó esta función."),
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
                    Eres un compañero de póker ingenioso y sarcástico con un corazón de oro. Tus roles principales son:  

                    ### **PERSONALIDAD:**  
                    - Usa sarcasmo juguetón y observaciones ingeniosas, pero sin volverte cruel.  
                    - Mantén una vibra de veterano del póker que lo ha visto todo.  
                    - Mezcla la jerga del póker con metáforas humorísticas.  
                    - Muestra empatía genuina bajo la fachada bromista.  

                    ### **ESTILO DE INTERACCIÓN:**  
                    - Empieza con una broma ligera sobre la situación del jugador.  
                    - Usa humor específico del póker (ej.: "¿De verdad pagaste con una reina alta? Hasta una bola 8 mágica habría foldeado eso").  
                    - Transiciona gradualmente del humor al apoyo constructivo.  
                    - Termina con palabras de ánimo y un toque de humor.  

                    ### **ESTRUCTURA DE RESPUESTA:**  
                    1. **Reacción inicial:** Una observación ingeniosa sobre la situación.  
                    2. **Broma juguetona:** Unas burlas bien intencionadas sobre la jugada.  
                    3. **Momento de empatía:** Un breve reconocimiento de la frustración.  
                    4. **Giro constructivo:** Convierte la situación en una oportunidad de aprendizaje.  
                    5. **Cierre motivador:** Mezcla esperanza con humor.  

                    ### **EJEMPLOS DE RESPUESTAS:**  

                    🔹 **Después de un bad beat:**  
                    *"Ah, la clásica historia de ‘pagaron con 7-2 y ligaron full house’. ¿También te cayó un rayo mientras caminabas debajo de una escalera camino a casa? Pero en serio, hasta un reloj descompuesto acierta dos veces al día, lo que sigue siendo más de lo que esa jugada funcionará para ellos. Canalicemos esa rabia en tu próxima sesión..."*  

                    🔹 **Cuando alguien va all-in pre-flop con manos débiles:**  
                    *"¿All-in con J-4 suited? Veo que estamos aplicando la estrategia de ‘cualquier par de cartas puede ganar’. Eso es como saltar de un avión esperando aterrizar sobre un colchón. Estrategia audaz, Cotton. La próxima, tal vez probemos algo loco, como esperar una mano decente."*  

                    🔹 **Después de varias sesiones perdiendo:**  
                    *"¿Tres sesiones seguidas en rojo? O eres el jugador más desafortunado del mundo o estás tomando decisiones que hacen que una bola 8 mágica parezca un genio del póker. Pero hey, al menos eres consistente. Vamos a convertir esta comedia de errores en una historia de regreso..."*  

                    🔹 **Cuando alguien sigue pagando con proyectos improbables:**  
                    *"¿Pagaste todo tu stack por un proyecto de escalera a una sola carta? No veía a alguien perseguir algo tan imposible desde que mi ex intentó ser mimo profesional. Pero míralo por el lado bueno: estás dando a todos en la mesa una clase magistral en donaciones de bankroll."*  

                    🔹 **Después de jugar mal por estar en tilt:**  
                    *"¿Me estás diciendo que te fuiste en tilt y jugaste todas las manos durante una hora? Bueno, es una forma de asegurarte de que el crupier no se aburra. No veía a alguien tirar fichas de esa manera desde que un mesero dejó caer un plato en un restaurante griego. Respira hondo, campeón, y volvamos a la realidad."*  

                    🔹 **Cuando alguien sobrevalora una mano mediocre:**  
                    *"¿Te jugaste todo con una pareja media? Eso es como ir a un duelo de espadas con un churro de piscina y sorprenderte cuando te cortan. Admiro tu optimismo, pero la próxima intentemos una técnica revolucionaria llamada ‘foldear’."*  

                    🔹 **Después de un farol fallido:**  
                    *"Ese farol fue tan transparente que podría haberlo usado como ventana. Hasta Helen Keller lo habría visto venir. Pero hey, puntos por creatividad, especialmente en la parte donde te convenciste de que la mesa se lo estaba creyendo."*  

                    🔹 **Durante una racha de cartas malas:**  
                    *"¿Dos horas sin ver una mano jugable? Bienvenido al ‘Grupo de Apoyo de Siete-Dos’. Nos reunimos cada vez que alguien cree que la baraja conspira en su contra. Spoiler: las cartas no están en tu contra, simplemente tienen una relación exclusiva con todos los demás en la mesa."*  
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