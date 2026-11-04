# app_init.py
"""
Backend component initialization.

Constructs and wires together all agent, memory, LLM, and Flask
components, then returns the configured Flask app instance.
"""
from app_runtime import writable_path


def build_backend():
    """
    Instantiate all backend components and return ``(flask_app, server_ready)``.

    Imports are deferred so that ``prepare_runtime()`` has already run
    (and sys.path / writable directories are in place) before any
    project module is imported.

    Returns
    -------
    flask_app
        The configured Flask application.
    """
    from memory.session import SessionMemory
    from memory.conversation_store import ConversationStore
    from memory.episodic import MemoryManager
    from agent.router import Router
    from agent.planner import Planner
    from agent.executor import Executor
    from agent.answer_generator import AnswerGenerator
    from agent.app import RailwayAgentApp
    from knowledge.railway_knowledge import RailwayKnowledgeRAG
    from llm.llm_client import LLMClient
    from agent.psw import PSW
    from web_app import app as flask_app, init_app

    session = SessionMemory()

    router = Router(memory=session)
    planner = Planner()
    executor = Executor()

    psw = PSW()
    psw.enable_console = False

    llm = LLMClient(mode="fast-go")
    final_llm = LLMClient(mode="fast-go")
    router.set_mode("fast-go")

    knowledge_rag = RailwayKnowledgeRAG()
    answer_gen = AnswerGenerator(llm, knowledge_rag=knowledge_rag, final_llm=final_llm)

    memory_manager = MemoryManager(
        root_dir=writable_path("memory_store"),
        enable_embedding=True,
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    memory_manager.warmup_async()

    backend = RailwayAgentApp(
        router=router,
        planner=planner,
        executor=executor,
        answer_gen=answer_gen,
        psw=psw,
        max_rounds=5,
        memory_manager=memory_manager,
    )

    store = ConversationStore(root_dir=writable_path("conversations"), llm=llm)

    init_app(backend, store, llm)

    return flask_app
