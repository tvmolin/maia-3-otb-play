import json
import secrets
from pathlib import Path


DEFAULT_PLAYER_ELO = 800
K_FACTOR = 40
MAX_PLAYER_ELO = 5000
RATING_CATEGORIES = ("blitz", "rapid")
OPPONENT_ADJECTIVES = (
    "Blunder",
    "Bouncing",
    "Caffeinated",
    "Chaotic",
    "Checkmating",
    "Forking",
    "Knightly",
    "Mischievous",
    "Sneaky",
    "Tactical",
    "Unstoppable",
    "Wandering",
    "Wooden",
    "Happy"
)
OPPONENT_NOUNS = (
    "Bishop",
    "Checkers",
    "Dragon",
    "EnPassant",
    "Fork",
    "Goblin",
    "Horsey",
    "Knight",
    "Juicer",
    "Shield",
    "Llama",
    "Pawn",
    "Rook",
    "Wizard",
)


class LadderStore:
    def __init__(self, path, initial_elo=DEFAULT_PLAYER_ELO):
        self.path = Path(path)
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate_data(data)
            self.ratings = data["ratings"]
            self.matches = data["matches"]
            self.initial_ratings = data.get("initial_ratings")
            if self.initial_ratings is None:
                self.initial_ratings = self._derive_initial_ratings(data)
        else:
            self.ratings = {category: initial_elo for category in RATING_CATEGORIES}
            self.matches = {category: [] for category in RATING_CATEGORIES}
            self.initial_ratings = dict(self.ratings)

    def rating_for(self, category):
        self._validate_category(category)
        return self.ratings[category]

    def matches_for(self, category):
        self._validate_category(category)
        return self.matches[category]

    def generate_opponent_name(self):
        used_names = {
            match.get("opponent_name")
            for matches in self.matches.values()
            for match in matches
            if isinstance(match.get("opponent_name"), str)
        }
        while True:
            name = (
                f"{secrets.choice(OPPONENT_ADJECTIVES)}"
                f"{secrets.choice(OPPONENT_NOUNS)}"
                f"{secrets.randbelow(9000) + 1000}"
            )
            if name not in used_names:
                return name

    def record_match(self, match):
        category = match.get("rating_category")
        self._validate_category(category)
        for category_matches in self.matches.values():
            for existing in category_matches:
                if existing["match_id"] == match["match_id"]:
                    return existing

        record, new_elo = self._calculate_record(match, self.ratings[category])
        ratings = {**self.ratings, category: new_elo}
        matches = {**self.matches, category: [*self.matches[category], record]}
        self._save(ratings, matches)
        self.ratings = ratings
        self.matches = matches
        return record

    def delete_match(self, category, match_id):
        self._validate_category(category)
        category_matches = self.matches[category]
        removed = next(
            (match for match in category_matches if match["match_id"] == match_id),
            None,
        )
        if removed is None:
            return None

        remaining = [
            match for match in category_matches if match["match_id"] != match_id
        ]
        player_elo = self.initial_ratings[category]
        recalculated = []
        for match in remaining:
            record, player_elo = self._calculate_record(match, player_elo)
            recalculated.append(record)

        ratings = {**self.ratings, category: player_elo}
        matches = {**self.matches, category: recalculated}
        self._save(ratings, matches)
        self.ratings = ratings
        self.matches = matches
        return removed

    @staticmethod
    def _calculate_record(match, player_elo):
        rated = match.get("rated", True)
        if not isinstance(rated, bool):
            raise ValueError("Match rated status must be a boolean.")
        try:
            player_score = LadderStore._player_score(
                match.get("result"), match.get("player_color")
            )
        except ValueError:
            player_score = match.get("score")
            if (
                isinstance(player_score, bool)
                or player_score not in (0, 0.5, 1)
            ):
                player_score = {
                    "win": 1.0,
                    "draw": 0.5,
                    "loss": 0.0,
                }.get(match.get("outcome"))
            if player_score not in (0, 0.5, 1):
                raise ValueError(
                    "Cannot recalculate a match without a valid player result."
                )
        if rated:
            maia_elo = match.get("maia_elo")
            if isinstance(maia_elo, bool) or not isinstance(maia_elo, (int, float)):
                raise ValueError("Cannot recalculate a match without a valid Maia rating.")
            expected_score = 1 / (1 + 10 ** ((maia_elo - player_elo) / 400))
            new_elo = round(player_elo + K_FACTOR * (player_score - expected_score))
            new_elo = min(MAX_PLAYER_ELO, max(0, new_elo))
        else:
            new_elo = player_elo
        record = {
            **match,
            "rated": rated,
            "player_elo_before": player_elo,
            "player_elo_after": new_elo,
            "rating_change": new_elo - player_elo,
            "score": player_score,
            "outcome": (
                "win" if player_score == 1 else "loss" if player_score == 0 else "draw"
            ),
            "k_factor": K_FACTOR if rated else None,
        }
        return record, new_elo

    @staticmethod
    def _derive_initial_ratings(data):
        initial_ratings = {}
        for category in RATING_CATEGORIES:
            history = data["matches"][category]
            rating = (
                history[0].get("player_elo_before")
                if history
                else data["ratings"][category]
            )
            if (
                isinstance(rating, bool)
                or not isinstance(rating, int)
                or not 0 <= rating <= MAX_PLAYER_ELO
            ):
                rating = data["ratings"][category]
            initial_ratings[category] = rating
        return initial_ratings

    def _save(self, ratings, matches):
        data = {
            "ratings": ratings,
            "matches": matches,
            "initial_ratings": self.initial_ratings,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary_path.write_text(
                json.dumps(data, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise

    @staticmethod
    def _validate_category(category):
        if category not in RATING_CATEGORIES:
            raise ValueError("Rating category must be blitz or rapid.")

    @staticmethod
    def _player_score(result, player_color):
        if result not in ("1-0", "0-1", "1/2-1/2"):
            raise ValueError("Cannot calculate a rating for an unfinished or invalid result.")
        if player_color not in ("white", "black"):
            raise ValueError("Cannot calculate a rating for an invalid player color.")
        if result == "1/2-1/2":
            return 0.5
        player_won = (result == "1-0") == (player_color == "white")
        return 1.0 if player_won else 0.0

    @staticmethod
    def _validate_data(data):
        if not isinstance(data, dict):
            raise ValueError("Ladder data must be a JSON object.")
        ratings = data.get("ratings")
        matches = data.get("matches")
        initial_ratings = data.get("initial_ratings")
        if (
            not isinstance(ratings, dict)
            or set(ratings) != set(RATING_CATEGORIES)
            or any(
                isinstance(rating, bool)
                or not isinstance(rating, int)
                or not 0 <= rating <= MAX_PLAYER_ELO
                for rating in ratings.values()
            )
            or not isinstance(matches, dict)
            or set(matches) != set(RATING_CATEGORIES)
            or any(
                not isinstance(category_matches, list)
                or any(
                    not isinstance(match, dict)
                    or not isinstance(match.get("match_id"), str)
                    for match in category_matches
                )
                for category_matches in matches.values()
            )
            or (
                initial_ratings is not None
                and (
                    not isinstance(initial_ratings, dict)
                    or set(initial_ratings) != set(RATING_CATEGORIES)
                    or any(
                        isinstance(rating, bool)
                        or not isinstance(rating, int)
                        or not 0 <= rating <= MAX_PLAYER_ELO
                        for rating in initial_ratings.values()
                    )
                )
            )
        ):
            raise ValueError("Ladder data has an invalid player rating or match history.")
