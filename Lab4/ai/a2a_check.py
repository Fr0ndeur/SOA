"""Discover producer and consumer, delegate one real session, inspect Task lifecycle."""
import json
import os

from a2a_agent import discover, message, rpc
from time_tools import EMAIL


def main():
    producer = os.getenv("PRODUCER_URL", "http://localhost:8101")
    consumer = os.getenv("CONSUMER_URL", "http://localhost:8102")
    for origin in (producer, consumer):
        card, _ = discover(origin)
        print("AGENT CARD", json.dumps(card))
    _, endpoint = discover(producer)
    params = message({"email": EMAIL, "duration_seconds": 600})
    result = rpc(endpoint, "SendMessage", params)
    task = result["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED", task
    assert "consumer_task" in task["artifacts"][0]["parts"][0]["data"]
    fetched = rpc(endpoint, "GetTask", {"id": task["id"]})
    assert fetched == task
    repeated = rpc(endpoint, "SendMessage", params)
    assert repeated["task"]["id"] == task["id"], "Retry created another task"
    listing = rpc(endpoint, "ListTasks", {"contextId": task["contextId"], "pageSize": 10})
    assert task["id"] in {item["id"] for item in listing["tasks"]}
    print("COMPLETED TASK", json.dumps(task))
    print("A2A CHECK PASSED: discovery, delegation, task retrieval, idempotent message retry")


if __name__ == "__main__":
    main()
