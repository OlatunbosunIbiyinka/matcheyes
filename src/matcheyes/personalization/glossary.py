"""What each metric measures, in plain words (from the Definition rows of docs/metrics.md).

A definition says what is counted. It says nothing about why a change happened or what it led
to, so it can be shown beside any insight without adding a claim. Entries are checked against
the calibration lexicon (no causal, certainty or intent language).
"""

GLOSSARY: dict[str, str] = {
    "on_ball_share": "On-ball share is the team's share of all passes, carries, take-ons and "
    "shots: a rough stand-in for possession.",
    "field_tilt": "Field tilt is the team's share of the on-ball actions that start in the "
    "attacking third: where the play is happening.",
    "high_regains": "High regains are the times the team won the ball back within 40 metres of "
    "the opponent's goal line.",
    "pressures": "Pressures are the times the team's players closed down an opponent on the ball.",
    "pressure_regain_rate": "Pressure regain rate is the share of the team's pressures after "
    "which the team itself was next to control the ball, within 5 seconds.",
    "defensive_action_height": "Defensive action height is how far from their own goal, on "
    "average, the team made its tackles, interceptions, recoveries, blocks, clearances and "
    "fouls.",
    "progressive_actions": "Progressive actions are completed passes and carries that move the "
    "ball substantially closer to the opponent's goal.",
    "attacking_third_entries": "Attacking-third entries are completed passes and carries that "
    "start outside the attacking third and end inside it.",
    "wide_share": "Wide share is the share of the team's completed open-play passes into the "
    "opponent's half that end in a wide channel.",
    "pass_completion": "Pass completion is the share of the team's open-play passes that are "
    "completed.",
    "shots": "Shots are the team's attempts on goal, of any kind.",
    "turnovers": "Turnovers are the team's possessions that end with the ball lost in open play.",
}
