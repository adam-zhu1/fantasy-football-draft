"""Team strength that learns from what teams actually score.

The season outlook used to rate every team by its draft-day projection and never update.
A team could score 60 a week for a month and still be rated by what its roster looked like
in September. That is why one week of results barely moved the playoff and last-place
numbers, and why they would have stayed vague all season.

This blends the draft projection with actual scoring, weighted by how much evidence there
is. The weight comes from two variances rather than a guessed constant:

  sigma^2  how much a team's score bounces week to week around its own true level
  tau^2    how far a team's true level sits from what its draft projection claimed

With n weeks played, the weight on actual scoring is

  w = (n / sigma^2) / (1 / tau^2 + n / sigma^2)

so one noisy week barely moves a rating, and by midseason results dominate. Both variances
are estimated from the league's own scoring once there is enough of it, and fall back to
sane defaults before that.
"""
import numpy as np

# Used only until week MIN_WEEKS_TO_ESTIMATE, after which both come from the league's own
# scoring. SIGMA is the week-to-week spread of a team total, which Week 1 2026 put at 21-26.
#
# TAU is how wrong a draft projection can be about a team's true level, and it is the one
# judgement call here. Setting it near 8 amounts to claiming the draft projections carry no
# information at all about which teams are good, since true team levels themselves spread by
# roughly that much. Setting it near 3 claims they are nearly right. They are built from
# season-long player projections and do carry some signal while badly compressing the spread
# between teams, so 6 sits between those claims.
#
# Early-season output is sensitive to this. After Week 1 2026, the bottom team's last-place
# probability ranged from 29% at tau=3 to 87% at tau=25. Treat week 1 and 2 ratings as
# provisional; from week 3 the data decides.
SIGMA_DEFAULT = 21.0
TAU_DEFAULT = 6.0
TAU_RANGE = (3.0, 25.0)
SIGMA_RANGE = (12.0, 32.0)
MIN_WEEKS_TO_ESTIMATE = 3


def _sigma(scores_by_team):
    """Week-to-week spread within a team, pooled across teams."""
    devs = []
    for s in scores_by_team.values():
        if len(s) >= 2:
            devs.extend(np.asarray(s, float) - np.mean(s))
    if len(devs) < 8:
        return SIGMA_DEFAULT
    n_teams = sum(1 for s in scores_by_team.values() if len(s) >= 2)
    dof = max(1, len(devs) - n_teams)
    return float(np.clip(np.sqrt(np.sum(np.square(devs)) / dof), *SIGMA_RANGE))


def _tau(prior_mu, scores_by_team, sigma):
    """How far true team strength sits from the draft projection.

    Var(observed mean - projection) = tau^2 + sigma^2 / n, so subtract the sampling term.
    """
    gaps, ns = [], []
    for t, s in scores_by_team.items():
        if t in prior_mu and s:
            gaps.append(np.mean(s) - prior_mu[t]); ns.append(len(s))
    if len(gaps) < 6:
        return TAU_DEFAULT
    n_bar = float(np.mean(ns))
    var = float(np.var(gaps, ddof=1)) - sigma ** 2 / max(1.0, n_bar)
    return float(np.clip(np.sqrt(max(var, 0.0)), *TAU_RANGE))


def team_ratings(prior_mu, scores_by_team):
    """Per-game scoring rate for each team, blending projection with actual results.

    prior_mu:        {team: draft-day expected points per week}
    scores_by_team:  {team: [week 1 score, week 2 score, ...]} for completed weeks

    Returns (ratings, detail) where detail carries the fitted sigma, tau, and each team's
    weight on actual scoring, so the report can say how much it is leaning on results.
    """
    weeks = max((len(s) for s in scores_by_team.values()), default=0)
    if weeks == 0:
        return dict(prior_mu), {"sigma": SIGMA_DEFAULT, "tau": TAU_DEFAULT, "weeks": 0, "weight": {}}

    if weeks >= MIN_WEEKS_TO_ESTIMATE:
        sigma = _sigma(scores_by_team)
        tau = _tau(prior_mu, scores_by_team, sigma)
    else:
        sigma, tau = SIGMA_DEFAULT, TAU_DEFAULT

    # The league's overall scoring level and the gaps between teams are different questions
    # and learn at different speeds. The level is an average over every team and week, so it
    # settles quickly; a single team's edge over the field does not. Correcting the level
    # first stops a prior that is systematically low from dragging every rating down for
    # months. In 2026 the draft projections sat about 13 points a week under what teams
    # actually scored, a bias shared by all twelve and therefore invisible in head to head.
    obs_means = {t: float(np.mean(s)) for t, s in scores_by_team.items() if s}
    shift = 0.0
    if obs_means:
        common = [t for t in obs_means if t in prior_mu]
        if common:
            n_obs = sum(len(scores_by_team[t]) for t in common)
            w_level = (n_obs / sigma ** 2) / (1.0 / TAU_RANGE[1] ** 2 + n_obs / sigma ** 2)
            raw = float(np.mean([obs_means[t] for t in common])
                        - np.mean([prior_mu[t] for t in common]))
            shift = w_level * raw
    prior_mu = {t: v + shift for t, v in prior_mu.items()}

    ratings, weight = {}, {}
    for t, mu0 in prior_mu.items():
        s = scores_by_team.get(t) or []
        n = len(s)
        if n == 0:
            ratings[t] = mu0; weight[t] = 0.0
            continue
        w = (n / sigma ** 2) / (1.0 / tau ** 2 + n / sigma ** 2)
        ratings[t] = (1 - w) * mu0 + w * float(np.mean(s))
        weight[t] = round(float(w), 3)
    return ratings, {"sigma": round(sigma, 1), "tau": round(tau, 1), "weeks": weeks,
                     "level_shift": round(shift, 1), "weight": weight}
