import itertools
from pathlib import Path
from random import Random
from typing import Callable, Iterable, Iterator, Optional, Generic, TypeVar, cast
import click

import requests

from schnapsen.bots import RandBot
from schnapsen_assignment.student import bot
from schnapsen_assignment.student.bot import AssignmentBot
from schnapsen.game import (BotState, GamePlayEngine, Move,
                            PlayerPerspective, SchnapsenDeckGenerator, SchnapsenHandGenerator,
                            SchnapsenTrickImplementer, SimpleMoveRequester,
                            SchnapsenMoveValidator, SchnapsenTrickScorer)

from schnapsen_assignment.serialization import GameLog, ConditionGameLog, ActionGameLog, to_schnapsen_move


@click.group(context_settings={'show_default': True})
def main() -> None:
    """The main entry point."""


@main.command(name="check", help="")
@click.option('--id', type=int, required=True, help="Your student ID")
def test_bot(id: int) -> None:
    # student_bot = AssignmentBot()
    student_bot = bot.AssignmentBot()
    game_cache = Path(".schnapsen_rollout_cache_{id}.json")
    if game_cache.exists():
        game_log = GameLog.FromString(game_cache.read_bytes())
    else:
        r = requests.get(f'https://krr.cs.vu.nl/prins/assignment/v1/<studentID>/bot.gamelog')
        if r.status_code != 200:
            raise Exception("Server could not be contacted")
        game_log = GameLog.FromString(r.content)
        # TODO save content to a caching file
    condition_errors, action_errors, integration_errors = assess_correctness(student_bot, id, game_log)
    print(f"""
Status report for {student_bot}.
    Condition 1: {'No errors found' if not condition_errors[0] else condition_errors[0][0]}
    Action 1: {'No errors found' if not action_errors[0] else action_errors[0][0]}
    Condition 2: {'No errors found' if not condition_errors[1] else condition_errors[1][0]}
    Condition 3: {'No errors found' if not condition_errors[2] else condition_errors[2][0]}
    Action 2: {'No errors found' if not action_errors[1] else action_errors[1][0]}
    Action 3: {'No errors found' if not action_errors[2] else action_errors[2][0]}
    Action 4: {'No errors found' if not action_errors[3] else action_errors[3][0]}

    Intergation test: {'No errors found' if not integration_errors[0] else integration_errors[0][0]}
""")


def assess_correctness(student_bot: AssignmentBot, id: int, game_log: GameLog) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    condition_errors: list[list[str]] = []
    condition_errors.append(assess_conditions_correctness(student_bot.condition1, id, game_log.condition1))
    condition_errors.append(assess_conditions_correctness(student_bot.condition2, id, game_log.condition2))
    condition_errors.append(assess_conditions_correctness(student_bot.condition3, id, game_log.condition3))

    action_errors: list[list[str]] = []
    conditions: Iterator[Iterator[bool]] = iter([iter([c for c in cond_log.outcomes]) for cond_log in game_log.condition1])
    action_errors.append(assess_actions_correctness(student_bot.action1, id, game_log.action1, conditions=conditions))
    action_errors.append(assess_actions_correctness(student_bot.action2, id, game_log.action2, conditions=None))
    action_errors.append(assess_actions_correctness(student_bot.action3, id, game_log.action3, conditions=None))
    action_errors.append(assess_actions_correctness(student_bot.action4, id, game_log.action4, conditions=None))

    integration_errors: list[list[str]] = []
    integration_errors.append(assess_integration_correctness(student_bot, id, game_log.integration))

    return condition_errors, action_errors, integration_errors


T = TypeVar('T', bound=bool | Move)


class CheckingGamePlayEngine(Generic[T], GamePlayEngine):

    class CheckingRequester(SimpleMoveRequester):
        def __init__(self, implementation: Callable[[PlayerPerspective, Optional[Move]], T],
                     expected_outcomes: Iterable[T],
                     conditions: Iterator[bool]
                     ) -> None:
            self.expected_outcomes_iterator: Iterator[T] = iter(expected_outcomes)
            self.conditions = conditions
            self.implementation = implementation
            self.errors: list[str] = []

        def get_move(self, bot: BotState, perspective: PlayerPerspective, leader_move: Move | None) -> Move:
            # check whether the condition working correctly
            if next(self.conditions):
                try:
                    outcome = self.implementation(perspective, leader_move)
                    expected_outcome = next(self.expected_outcomes_iterator)
                    if outcome != expected_outcome:
                        self.errors.append(f"Something is wrong with the condition {self.implementation}. For input {perspective}, {expected_outcome} was expected, but got {outcome}.")
                except Exception as e:
                    self.errors.append(f"An exception was raised from {self.implementation} with message: {e}")

            bot_move = super().get_move(bot, perspective, leader_move)
            return bot_move

    def __init__(self, implementation: Callable[[PlayerPerspective, Optional[Move]], T],
                 expected_outcomes: Iterable[T],
                 conditions: Iterator[bool]
                 ):
        super().__init__(deck_generator=SchnapsenDeckGenerator(),
                         hand_generator=SchnapsenHandGenerator(),
                         trick_implementer=SchnapsenTrickImplementer(),
                         move_requester=CheckingGamePlayEngine.CheckingRequester(implementation, expected_outcomes, conditions),
                         move_validator=SchnapsenMoveValidator(),
                         trick_scorer=SchnapsenTrickScorer())

    def errors(self) -> list[str]:
        req = cast(CheckingGamePlayEngine.CheckingRequester, self.move_requester)
        return req.errors


def assess_conditions_correctness(condition_implementation: Callable[[PlayerPerspective, Optional[Move]], bool],
                                  id: int, condition_game_logs: Iterable[ConditionGameLog]) -> list[str]:
    for condition_game_log in condition_game_logs:
        game_id = condition_game_log.game_id
        outcomes: Iterable[bool] = condition_game_log.outcomes
        engine = CheckingGamePlayEngine(condition_implementation, outcomes, itertools.repeat(True))

        randbot = RandBot(Random(12345678910 + game_id))
        engine.play_game(randbot, randbot, Random(game_id))
        if engine.errors():
            # We stop early to not report 100s of times the same error
            return engine.errors()
    return []


def assess_actions_correctness(action_implementation: Callable[[PlayerPerspective, Optional[Move]], Move],
                               id: int, action_game_logs: Iterable[ActionGameLog],
                               conditions: Iterator[Iterator[bool]] | None) -> list[str]:

    if conditions is None:
        conditions = map(itertools.repeat, itertools.repeat(True))

    for action_game_log, condition in zip(action_game_logs, conditions):
        game_id = action_game_log.game_id
        outcomes: Iterable[Move] = map(to_schnapsen_move, action_game_log.outcomes)
        engine = CheckingGamePlayEngine(action_implementation, outcomes, condition)

        randbot = RandBot(Random(12345678910 + game_id))
        engine.play_game(randbot, randbot, Random(game_id))
        if engine.errors():
            # We stop early to not report 100s of times the same error
            return engine.errors()
    return []


class IntegrationCheckingGamePlayEngine(GamePlayEngine):

    class CheckingRequester(SimpleMoveRequester):
        def __init__(self, bot: AssignmentBot,
                     expected_outcomes: Iterable[Move],
                     ) -> None:
            self.expected_outcomes_iterator: Iterator[Move] = iter(expected_outcomes)
            self.bot = bot
            self.errors: list[str] = []

        def get_move(self, bot: BotState, perspective: PlayerPerspective, leader_move: Move | None) -> Move:
            # check whether the condition working correctly
            expected_move: Move = next(self.expected_outcomes_iterator)
            if bot.implementation == self.bot:
                try:
                    bot_move = super().get_move(bot, perspective, leader_move)
                except Exception as e:
                    self.errors.append(f"An exception was raised your bot with message: {e}")
                    bot_move = expected_move
                if bot_move != expected_move:
                    self.errors.append(f"Bot played a wrong move. For input {perspective}, {expected_move} was expected, but got {bot_move}.")
                    bot_move = expected_move
            else:
                bot_move = super().get_move(bot, perspective, leader_move)

            return bot_move

    def __init__(self, bot: AssignmentBot, expected_outcomes: Iterable[Move]):
        super().__init__(deck_generator=SchnapsenDeckGenerator(),
                         hand_generator=SchnapsenHandGenerator(),
                         trick_implementer=SchnapsenTrickImplementer(),
                         move_requester=IntegrationCheckingGamePlayEngine.CheckingRequester(bot, expected_outcomes),
                         move_validator=SchnapsenMoveValidator(),
                         trick_scorer=SchnapsenTrickScorer())

    def errors(self) -> list[str]:
        req = cast(IntegrationCheckingGamePlayEngine.CheckingRequester, self.move_requester)
        return req.errors


def assess_integration_correctness(student_bot: AssignmentBot, id: int, game_logs: Iterable[ActionGameLog]) -> list[str]:
    for game_log in game_logs:
        game_id = game_log.game_id
        outcomes: Iterable[Move] = [to_schnapsen_move(move) for move in game_log.outcomes]
        engine = IntegrationCheckingGamePlayEngine(student_bot, outcomes)

        randbot = RandBot(Random(12345678910 + game_id))
        engine.play_game(student_bot, randbot, Random(game_id))
        if engine.errors():
            # We stop early to not report 100s of times the same error
            return engine.errors()
    return []


if __name__ == "__main__":
    main()
