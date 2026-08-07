"""Regression tests for lotto scoreboard rewards."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from endstone_lotto.lotto_plugin import LottoPlugin


class FakeLogger:
    def __init__(self):
        self.errors = []

    def info(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, message):
        self.errors.append(message)


class FakeScore:
    def __init__(self, value=None):
        self._value = value
        self.is_score_set = value is not None

    @property
    def value(self):
        return self._value if self.is_score_set else 0

    @value.setter
    def value(self, value):
        self._value = value
        self.is_score_set = True


class FakePlayer:
    def __init__(self, name):
        self.name = name
        self.messages = []
        self.errors = []

    def send_message(self, message):
        self.messages.append(message)

    def send_error_message(self, message):
        self.errors.append(message)


class RewardTests(unittest.TestCase):
    def test_change_money_uses_writable_score_api(self):
        score = FakeScore(250)
        harness = SimpleNamespace(
            logger=FakeLogger(),
            _get_money_score=lambda _player: score,
        )

        self.assertTrue(LottoPlugin._change_money(harness, FakePlayer("Winner"), 1000))
        self.assertEqual(score.value, 1250)

    def test_failed_online_payout_is_saved_for_retry(self):
        player = FakePlayer("Winner")
        config = SimpleNamespace(
            get_lotto_type=lambda _lotto_id: {
                "id": "weekly",
                "display_name": "Weekly Lotto",
                "prize_amount": 1000,
                "entries": {"Winner": 1},
            },
            add_pending_reward=Mock(),
            clear_entries=Mock(),
            update_last_draw_time=Mock(),
        )
        server = SimpleNamespace(
            get_player=lambda _name: player,
            broadcast_message=Mock(),
        )
        harness = SimpleNamespace(
            lotto_config=config,
            server=server,
            logger=FakeLogger(),
            give_money=lambda _player, _amount: False,
        )

        LottoPlugin.perform_draw(harness, "weekly")

        config.add_pending_reward.assert_called_once_with("Winner", "weekly", 1000)
        config.clear_entries.assert_called_once_with("weekly")
        self.assertTrue(player.errors)

    def test_join_keeps_only_rewards_that_failed(self):
        player = FakePlayer("Winner")
        rewards = [
            {"lotto_id": "first", "amount": 100},
            {"lotto_id": "second", "amount": 200},
        ]
        saved = []
        config = SimpleNamespace(
            get_pending_rewards=lambda _name: rewards,
            get_lotto_type=lambda lotto_id: {"display_name": lotto_id.title()},
            set_pending_rewards=lambda name, remaining: saved.append((name, remaining)),
        )
        harness = SimpleNamespace(
            lotto_config=config,
            logger=FakeLogger(),
            give_money=lambda _player, amount: amount == 100,
        )

        LottoPlugin.on_player_join(harness, SimpleNamespace(player=player))

        self.assertEqual(saved, [("Winner", [rewards[1]])])
        self.assertEqual(len(player.messages), 1)
        self.assertEqual(len(player.errors), 1)


if __name__ == "__main__":
    unittest.main()
