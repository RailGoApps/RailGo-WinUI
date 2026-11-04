from enum import Enum

class AgentStage(str, Enum):
    ROUTING = "routing"
    PLANNING = "planning"
    EXECUTING = "executing"
    ANSWERING = "answering"
    DONE = "done"
    ERROR = "error"