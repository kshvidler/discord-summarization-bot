import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from os import environ as env
from deepgram import DeepgramClient, PrerecordedOptions, FileSource
import os
import asyncio

# Load environment variables
load_dotenv(override=True)

# Setup logging to output to the console
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True  # Enables reading message content for command processing

discord.opus.load_opus("/opt/homebrew/lib/libopus.0.dylib")

# Initialize the bot with command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

connections = {}

# Initialize Deepgram Client with your API key
deepgram = DeepgramClient(env.get("DEEPGRAM_API_KEY"))

# Define transcription options for Deepgram
options = PrerecordedOptions(
    model="nova-2-meeting",
    smart_format=True,
    utterances=False,
    punctuate=True,
    diarize=True,
    detect_language=True,
    summarize='v2'
)

# Log when the bot is ready
@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logging.info(f"Opus: {discord.opus.is_loaded()}")
    logging.info('------')

@bot.command()
async def record(ctx):
    """Command to start recording in a voice channel."""
    voice = ctx.author.voice

    if not voice:
        await ctx.send("⚠️ You aren't in a voice channel!")
        return

    vc = await voice.channel.connect()
    connections.update({ctx.guild.id: vc})

    # Start recording using WaveSink to capture audio
    vc.start_recording(
        discord.sinks.WaveSink(),
        once_done,  # Callback function when recording is finished
        ctx.channel,
    )
    await ctx.send("🔴 Listening to this conversation.")

async def once_done(sink: discord.sinks, channel: discord.TextChannel, *args):
    """Callback to process audio once recording is finished."""
    recorded_users = [f"<@{user_id}>" for user_id, audio in sink.audio_data.items()]
    
    # Disconnect from voice channel
    await sink.vc.disconnect()

    # Process recordings
    for user_id, audio in sink.audio_data.items():
        # Create a file path to save the recording
        file_path = f"{user_id}_recording.wav"
        
        # Save the audio data to a file
        with open(file_path, "wb") as f:
            f.write(audio.file.read())
        
        # Transcribe the file
        try:
            transcript, summary = await transcribe_audio_file(file_path)
            await send_transcript(channel, transcript, summary, f"{user_id}_recording", recorded_users)
        except Exception as e:
            await channel.send(f"An error occurred during transcription: {str(e)}")
            return
        finally:
            # Clean up the local file after transcription is complete
            os.remove(file_path)

@bot.command()
async def stop_recording(ctx):
    """Command to stop recording."""
    if ctx.guild.id in connections:
        vc = connections[ctx.guild.id]
        vc.stop_recording()
        del connections[ctx.guild.id]
        await ctx.send("🛑 Stopped recording.")
    else:
        await ctx.send("🚫 Not recording in this channel.")

@bot.command()
async def transcribe_files(ctx):
    """Command to transcribe local .wav files and send the transcript to a text channel."""
    # Directory where the .wav files are stored
    directory = "./"  # Change this to the directory containing your .wav files

    # Get a list of .wav files in the directory
    wav_files = [f for f in os.listdir(directory) if f.endswith('.wav')]

    if not wav_files:
        await ctx.send("No .wav files found in the directory.")
        return

    await ctx.send(f"Found {len(wav_files)} .wav files. Starting transcription...")

    # Process each file
    for file_name in wav_files:
        file_path = os.path.join(directory, file_name)
        # Transcribe the file
        try:
            transcript, summary = await transcribe_audio_file(file_path)
            await send_transcript(ctx.channel, transcript, summary, file_name)
        except Exception as e:
            await ctx.send(f"An error occurred during transcription of {file_name}: {str(e)}")
            continue

    await ctx.send("Transcription completed.")

async def transcribe_audio_file(file_path):
    # Prepare the file for Deepgram transcription
    with open(file_path, "rb") as audio_file:
        payload: FileSource = {
            "buffer": audio_file.read(),
        }

    # Run the transcription in a separate thread to prevent blocking
    response = await asyncio.to_thread(deepgram_transcribe_file, payload, options)

    # Process the words and generate the transcript
    words = response["results"]["channels"][0]["alternatives"][0]["words"]
    summary = response["results"]["summary"]["short"]  # Get the summary

    transcript = ""
    current_speaker = None
    for word in words:
        if word["speaker"] != current_speaker:
            # Add speaker info before their dialog
            transcript += f"\n\nSpeaker {word['speaker']}: "
            current_speaker = word["speaker"]
        transcript += f"{word['punctuated_word']} "

    # Return the transcript and summary separately
    return transcript.strip(), summary

def deepgram_transcribe_file(payload, options):
    response = deepgram.listen.rest.v("1").transcribe_file(payload, options)
    return response

async def send_transcript(channel, transcript, summary, file_name, recorded_users=None):
    # Create a temporary file to hold the transcript
    transcript_file_path = f"{file_name}_transcript.txt"
    with open(transcript_file_path, "w") as f:
        f.write(transcript)

    # Create a discord.File object
    transcript_file = discord.File(transcript_file_path)

    # Prepare the message content
    if recorded_users:
        content = f"Finished recording audio for: {', '.join(recorded_users)}.\n\n**Summary**: {summary}"
    else:
        content = f"Transcript for {file_name}:\n\n**Summary**: {summary}"

    # Send a message with the summary and the transcript file attached
    await channel.send(content=content, file=transcript_file)

    # Clean up the temporary file
    os.remove(transcript_file_path)

# Run the bot with the Discord token from the environment variables
bot.run(env.get("DISCORD_BOT_TOKEN"))
