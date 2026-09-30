# World: The Ultimate Rock, Paper, Scissors Arena

There is no elaborate map, hidden treasure, or world-saving quest. There is only an ordinary table, two passersby who have become inexplicably competitive, and rock, paper, scissors.

## Match rules

- This is a two-player match between Passerby A and Passerby B.
- Each game round is one round of rock, paper, scissors.
- Each player should clearly choose exactly one final throw: rock, paper, or scissors.
- Dialogue, taunts, feints, and dramatic declarations are allowed. The world updater must independently extract each player's final explicit throw. It must never change a throw for dramatic effect.
- If a player gives no clear throw, or names conflicting throws without a clear final choice, their throw is invalid. If either throw is invalid, nobody scores a win.
- Throws are simultaneous. The second player's choice cannot react to the first player's action text.
- Rock beats scissors; scissors beats paper; paper beats rock. Matching throws are a draw.
- Draws do not increase either player's wins.
- The first player to reach five wins takes the match. Display wins as current wins out of five, such as 3/5.
- Once somebody has five wins, the match is over. Later actions cannot change the final score or winner.

## Presentation

- Objective results must follow the actual throws and rules, regardless of dramatic style.
- An ordinary throw may be described with absurd intensity, but dramatic language never creates extra rules or supernatural powers.
- A short mood label may reflect streaks, a comeback, or match point: for example, 😎 Smug, 🔥 Fuming, 🗿 Unshaken, or 😵 In Disbelief. Mood does not affect the result.
- Spoken taunts and visible gestures may be known to both players. Private thoughts must remain private.

## Information boundaries

- After resolution, both players know the revealed throws, result, public score, and visible reactions or spoken words.
- Each character knows only their own unspoken thoughts, private tactics, and intentions.
- world_state holds the single complete match record. A character_view contains only information that character can actually know.

## Pace

Keep this short, fast, and funny. Do not add subplots, locations, third parties, equipment, combat statistics, or a grand mythology. Every round follows the same rhythm: throw, simultaneous reveal, result, one brief overdramatic reaction, then the next round.
