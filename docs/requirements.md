# Requirements

A large auto insurer processes a high volume of vehicle damage claims and wants to know whether AI can help.

## Input

One photograph of a damaged vehicle, submitted by file upload or by URL.

## Output

- **Vehicle metadata** — make, model, and colour.
- **Damage summary** — a description of the damage, for example "left rear bumper dent with scratching".
- **Estimated repair cost** — a rough AI-generated estimate.

## Evaluation

State how quality would be judged for a problem where the right metric is not obvious.

- **Working.** Which quality dimensions matter, and what is measured for each.
- **Failure.** Where the system fails, and which of those failures matter most to this carrier.
- **Customer inputs.** What is required from the carrier to evaluate properly: data, subject-matter expert access, historical baselines, and anything else.
- **Repair cost.** How to decide that an estimate is good enough to be useful, and what happens when it is wrong.
