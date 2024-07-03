import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material.Button
import androidx.compose.material.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.livekit.android.LiveKit
import io.livekit.android.room.Room
import io.livekit.android.room.participant.Participant
import io.livekit.android.room.track.Track
import io.livekit.android.util.flow

class MainActivity : ComponentActivity() {
    private lateinit var room: Room

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        room = LiveKit.create(applicationContext)

        setContent {
            MainScreen(room)
        }
    }
}

@Composable
fun MainScreen(room: Room) {
    var isConnected by remember { mutableStateOf(false) }
    var isAgentSpeaking by remember { mutableStateOf(false) }

    LaunchedEffect(room) {
        room.events.collect { event ->
            when (event) {
                is Room.TrackSubscribed -> {
                    if (event.participant.identity == "agent" && event.track.kind == Track.Kind.AUDIO) {
                        isAgentSpeaking = true
                    }
                }
                is Room.TrackUnsubscribed -> {
                    if (event.participant.identity == "agent" && event.track.kind == Track.Kind.AUDIO) {
                        isAgentSpeaking = false
                    }
                }
            }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        if (!isConnected) {
            Button(onClick = { connectToRoom(room) { isConnected = true } }) {
                Text("Connect to Agent")
            }
        } else {
            AgentAnimation(isAgentSpeaking)
            Spacer(modifier = Modifier.height(16.dp))
            Button(onClick = { toggleMicrophone(room) }) {
                Text("Toggle Microphone")
            }
        }
    }
}

@Composable
fun AgentAnimation(isAgentSpeaking: Boolean) {
    // Implement a simple animation here
    // For now, we'll just show text
    if (isAgentSpeaking) {
        Text("Agent is speaking...")
    } else {
        Text("Agent is listening...")
    }
}

fun connectToRoom(room: Room, onConnected: () -> Unit) {
    val url = BuildConfig.LIVEKIT_URL
    val apiKey = BuildConfig.LIVEKIT_API_KEY
    val apiSecret = BuildConfig.LIVEKIT_API_SECRET
    
    // Generate token (you might want to do this on your server instead)
    val token = AccessToken(apiKey, apiSecret).apply {
        addGrant(RoomJoin("your-room-name"))
        identity = "android-user"
    }.toJwt()

    room.connect(url, token)
    room.addListener(object : Room.Listener {
        override fun onParticipantConnected(participant: Participant) {
            // Check if the connected participant is the agent
            if (participant.identity == "agent") {
                onConnected()
            }
        }
    })
}

fun toggleMicrophone(room: Room) {
    room.localParticipant?.setMicrophoneEnabled(!room.localParticipant?.isMicrophoneEnabled!!)
}
fun connectToRoom(room: Room, onConnected: () -> Unit) {
    // Implement room connection logic here
    // You'll need to generate a token from your server
    val url = "wss://your-livekit-server-url"
    val token = "your-access-token"
    
    room.connect(url, token)
    room.addListener(object : Room.Listener {
        override fun onParticipantConnected(participant: Participant) {
            // Check if the connected participant is the agent
            if (participant.identity == "agent") {
                onConnected()
            }
        }
    })
}

fun toggleMicrophone(room: Room) {
    room.localParticipant?.setMicrophoneEnabled(!room.localParticipant?.isMicrophoneEnabled!!)
}