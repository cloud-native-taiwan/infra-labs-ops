"""Logic tests for verify-tuning.yml's live RabbitMQ queue-type check.

The check must mirror kolla-ansible's rabbitmq-ha-precheck durable-queue
assert (README prechecks skip that tag because reply queues are classic on
purpose): every queue that is not reply_* or *_fanout must be quorum.
"""

import json
import unittest
from pathlib import Path

from conftest import evaluate, iter_tasks, load_yaml, trusted

PLAYBOOK = Path(__file__).resolve().parents[1] / "playbooks" / "verify-tuning.yml"

# Shape of `rabbitmqctl list_queues --silent name type --formatter json`.
SAMPLE = json.dumps(
    [
        {"name": "conductor", "type": "quorum"},
        {"name": "compute.openstack05", "type": "quorum"},
        {"name": "reply_4f1c2b", "type": "classic"},
        {"name": "scheduler_fanout_9a8b", "type": "classic"},
        {"name": "cinder-volume", "type": "classic"},
    ]
)


def _live_task(name: str) -> dict:
    plays = load_yaml(PLAYBOOK)
    (play,) = [p for p in plays if p["name"] == "Verify RabbitMQ live cluster state"]
    (task,) = [t for t in iter_tasks(play["tasks"]) if t.get("name") == name]
    return task


class VerifyTuningRabbitMQTests(unittest.TestCase):
    def _facts(self) -> dict:
        task = _live_task("RabbitMQ live: parse queue types")
        facts = task["ansible.builtin.set_fact"]
        # Each fact is evaluated against stdout alone: set_fact cannot see a
        # sibling key from the same task.
        variables = trusted({"rmq_queues": {"stdout": SAMPLE, "rc": 0}})
        queue_list = evaluate(facts["rmq_queue_list"].strip("{} \n"), variables)
        offenders = evaluate(facts["rmq_service_queues_not_quorum"].strip("{} \n"), variables)
        return {"rmq_queue_list": queue_list, "offenders": offenders}

    def test_parses_rabbitmqctl_json_list(self) -> None:
        self.assertEqual(len(self._facts()["rmq_queue_list"]), 5)

    def test_only_durable_classic_queues_are_flagged(self) -> None:
        names = [q["name"] for q in self._facts()["offenders"]]
        self.assertEqual(names, ["cinder-volume"])

    def test_assert_uses_offender_list(self) -> None:
        task = _live_task("RabbitMQ live: assert service queues are quorum")
        self.assertIn("rmq_service_queues_not_quorum", str(task["ansible.builtin.assert"]["that"]))


if __name__ == "__main__":
    unittest.main()
