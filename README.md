# Playoff Line 2027

A Monte Carlo model that predicts the 2026–27 NBA playoff picture: which six teams in each conference finish 1 through 6 and go straight to the playoffs, and which four land in seeds 7 through 10 for the play-in tournament.

It runs two ways from the same data:

- **Python command line** (`python -m predictor`) for repeatable runs and CSV output
- **Interactive web page** in `docs/` that you can host free on GitHub Pages, with sliders, editable injuries and roster moves, and live re-simulation in the browser

```
WESTERN CONFERENCE
 #  Team                        Rec    Rtg  Moves   Inj  ProjW   Top6  PlayIn  Playoffs
 1  San Antonio Spurs           0-0   +5.9   +1.0     -   56.5    95%      5%       98%
 2  Oklahoma City Thunder       0-0   +6.1   -0.8  -0.8   56.4    94%      5%       98%
 3  Denver Nuggets              0-0   +2.6   +0.0  -0.2   48.1    74%     21%       86%
 ...
```

## How the model works

1. **Preseason rating.** Every team gets a rating in points of margin per game versus league average, blended from three inputs (default weights in brackets):
   - **Last season (30%)**: 2025–26 net rating, regressed 30% toward average, plus an adjustment for each offseason move (`data/moves.csv`). The moves table is what makes last season's number describe this season's roster.
   - **Preseason win total (60%)**: the betting line, converted at 2.7 wins per point of margin. Lines are scaled so the league totals exactly 1,230 wins.
   - **Preseason games (10%)**: exhibition record, smoothed with a 3–3 ghost record because four or five preseason games say very little.
2. **In-season blend.** Once games are played, each team's actual point differential is mixed in. The preseason rating counts as 25 games of evidence by default, so a team 25 games in is rated half on its preseason rating and half on its real margin.
3. **Injuries, game by game.** Each injury has an impact (net rating points) and a number of games out. In every simulated season the team plays weaker for exactly those games. Opponents' injuries count too.
4. **Simulate.** Each remaining game is played against a random opponent with home court worth 2.5 points, and a logistic curve turns the rating gap into a win probability. Each simulated season also draws a random shock to every team's rating, sized by how uncertain that rating still is.
5. **Seed and play in.** Teams are ranked by wins in each conference. Seeds 7 vs 8 and 9 vs 10 play, then the 7/8 loser hosts the 9/10 winner for the final playoff spot.

The output for each team is projected wins, the chance of a top 6 seed, the chance of landing in the play-in, and the overall chance of making the playoffs (top 6 or winning the play-in). Teams that have mathematically locked up a top 6 seed are marked clinched, and teams that can no longer reach the top 10 are marked eliminated (tiebreakers ignored).

## Quick start

```bash
git clone https://github.com/<your-username>/nba-playoff-predictor.git
cd nba-playoff-predictor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m predictor                         # 5,000 simulated seasons
python -m predictor --sims 10000 --seed 42  # repeatable run
python -m predictor --weights 40 50 10      # lean more on last season
python -m predictor --out output/projections.csv
pytest                                      # run the tests
```

Open the web version locally by double-clicking `docs/index.html`, or serve it with `python -m http.server -d docs`.

### Publish the web page with GitHub Pages

1. Push the repo to GitHub.
2. Go to **Settings → Pages**.
3. Under **Build and deployment**, pick **Deploy from a branch**, branch `main`, folder `/docs`, and save.
4. The page appears at `https://<your-username>.github.io/nba-playoff-predictor/` after a minute or two.

## Automatic daily updates

A scheduled GitHub Actions workflow (`.github/workflows/update-standings.yml`) keeps the standings current with no manual work:

```
ESPN standings feed ──► validate ──► data/standings.csv ──► run model (10,000 seasons) ──► docs/js/data.js
                                                                    │                       results/projections.csv
                                                                    └──────────────────────► results/history.csv
```

1. **Extract.** Every morning at about 4 AM Pacific, `scripts/update_standings.py` pulls the league standings from ESPN's public JSON feed (no API key needed).
2. **Validate.** The feed is unofficial, so the run stops without writing anything if any team is missing or unrecognized, a record is impossible, league wins and losses don't balance, a point differential is implausible, or a team's record went backwards since the last run.
3. **Transform and load.** ESPN team codes are mapped to the project's (for example `GS` to `GSW`), point differential per game is computed from points for and against, and `data/standings.csv` is rewritten.
4. **Model and publish.** The model reruns with a date-based seed so results are reproducible. The web page's data, the latest projections and a running history of every team's odds are refreshed, the tests run on the new data, and the changes are committed.

If there were no games since the last run, nothing is committed. If the feed fails validation, the run shows a red X in the Actions tab and the last good data stays in place. You can also start a run by hand from **Actions → update standings → Run workflow**.

`results/history.csv` has one row per team per day (projected wins, average seed, and the chance of a top 6 seed, play-in and playoffs), which makes it easy to chart how each team's odds moved over the season.

## Updating by hand

Injuries and roster moves need judgment, so they are not automated. All inputs are plain CSV files in `data/`:

| File | What it holds | How often to update |
|---|---|---|
| `standings.csv` | 2026–27 W, L and average point differential per game | Automatic, daily |
| `injuries.csv` | Player, impact in points, games still out | Weekly, as players return or get hurt |
| `moves.csv` | Offseason and in-season roster changes with impact in points | After trades |
| `teams.csv` | Names, conferences, win totals, 2025–26 record and net rating, preseason record | Once per season |

After editing a CSV, rebuild the web page's data so both versions agree:

```bash
python scripts/build_web_data.py --as-of "Nov 2, 2026"
```

You can also update standings in the browser (paste them into the Team stats panel) and use **Export standings CSV** to download a file that drops straight into `data/standings.csv`.

## Project structure

```
nba-playoff-predictor/
├── data/                    CSV inputs, the single source of truth
├── predictor/
│   ├── data_io.py           load and validate the CSVs
│   ├── model.py             ratings, injuries, season simulation, play-in, clinch logic
│   ├── standings_source.py  fetch and validate the ESPN standings feed
│   └── __main__.py          command line interface
├── scripts/
│   ├── update_standings.py  the daily pipeline: fetch, validate, write, rerun model
│   └── build_web_data.py    writes docs/js/data.js from the CSVs
├── results/                 projections.csv (latest run) and history.csv (every run), written by the pipeline
├── docs/                    the web app (GitHub Pages serves this folder)
│   ├── index.html
│   ├── css/styles.css
│   └── js/                  data.js (generated), model.js (same model as Python), app.js (UI)
├── tests/                   model tests and feed parsing/validation tests
└── .github/workflows/
    ├── tests.yml            runs on every push
    └── update-standings.yml runs every morning
```

`predictor/model.py` and `docs/js/model.js` implement the same model. If you change one, change the other.

## Data sources

- 2025–26 records and net ratings: [Basketball Reference](https://www.basketball-reference.com/leagues/NBA_2026_ratings.html)
- 2026–27 win totals: DraftKings lines as published by [Sports Illustrated](https://www.si.com/betting/nba-win-total-predictions-for-every-team-in-2026-27-season-thunder-spurs-only-teams-projected-to-win-60-games) on Oct 15, 2026
- 2026 preseason records: [RealGM](https://basketball.realgm.com/nba/preseason/standings)
- Injury statuses: [Covers](https://www.covers.com/sport/basketball/nba/injuries), Oct 19, 2026
- 2026–27 standings (updated daily): ESPN's public standings feed, which is unofficial and may change without notice

The impact values for injuries and roster moves are judgment-based estimates, not measured stats. They are meant to be edited.

## Limitations

- Opponents are drawn at random rather than from the real schedule, so strength of schedule and back-to-backs are not modeled.
- Ties in the standings are broken randomly instead of with the NBA's tiebreaker rules.
- The win total lines already price in injuries known before mid-October, so long absences are counted partly twice. Impacts are kept moderate to offset this.
- Player impact values are estimates. A player-level metric such as EPM or RAPTOR would make them more precise.

## Ideas for next steps

- Chart `results/history.csv` on the web page to show how each team's odds moved over the season
- Use the real 2026–27 schedule, including home and away and rest days
- Replace hand-set injury impacts with a player value metric
- Track projections over time to show how each team's odds moved

## License

MIT. Not affiliated with the NBA. For analysis and fun, not betting advice.
