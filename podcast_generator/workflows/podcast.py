"""LangGraph workflow that generates a podcast script and its audio."""

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from ..tools.podcast import generate_podcast_script_tool, make_podcast_mp3_tool


class PodcastState(TypedDict):
    source_pdf: str
    provider: str
    script_output_path: str
    audio_output_path: str
    model: NotRequired[str]
    topic: NotRequired[str]
    conversation_style: NotRequired[str]
    suggestions: NotRequired[str]
    tts_provider: NotRequired[str]
    tts_model: NotRequired[str]
    speaker_1_voice: NotRequired[str]
    speaker_2_voice: NotRequired[str]
    rate: NotRequired[str]
    script_path: NotRequired[str]
    audio_path: NotRequired[str]


def generate_script_node(state: PodcastState) -> dict[str, str]:
    """Run the script-generation tool and save its output path to graph state."""
    script_path = generate_podcast_script_tool.invoke(
        {
            "source_pdf": state["source_pdf"],
            "script_output_path": state["script_output_path"],
            "provider": state["provider"],
            "model": state.get("model", ""),
            "topic": state.get("topic", ""),
            "conversation_style": state.get("conversation_style", ""),
            "suggestions": state.get("suggestions", ""),
        }
    )
    return {"script_path": script_path}


def make_mp3_node(state: PodcastState) -> dict[str, str]:
    """Run the audio-generation tool after the script has been saved."""
    script_path = state.get("script_path")
    if script_path is None:
        raise ValueError("Script generation did not produce an output path.")
    audio_path = make_podcast_mp3_tool.invoke(
        {
            "script_path": script_path,
            "audio_output_path": state["audio_output_path"],
            "tts_provider": state.get("tts_provider", "edge"),
            "tts_model": state.get("tts_model", ""),
            "speaker_1_voice": state.get("speaker_1_voice", ""),
            "speaker_2_voice": state.get("speaker_2_voice", ""),
            "rate": state.get("rate", "+15%"),
        }
    )
    return {"audio_path": audio_path}


def build_podcast_graph():
    """Build the script-then-audio podcast generation workflow."""
    graph = StateGraph(PodcastState)
    graph.add_node("generate_script", generate_script_node)
    graph.add_node("make_mp3", make_mp3_node)
    graph.add_edge(START, "generate_script")
    graph.add_edge("generate_script", "make_mp3")
    graph.add_edge("make_mp3", END)
    return graph.compile()


podcast_graph = build_podcast_graph()
