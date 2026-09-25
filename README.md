# AI RP Engine

A lightweight multiplayer AI role-playing engine.

## Current Goal

Two players connect through terminal clients and explore an AI-managed shared world.

## Architecture

```
Player A
    |
Player B
    |
    v

Round Manager

    |
    v

World Update LLM

    |
    v

World State

    |
    +------------+
    |            |
    v            v

Narrator A   Narrator B
```

## Development

Install:

```bash
pip install -r requirements.txt
```

Run server:

```bash
python -m server.main
```

Run client:

```bash
python client/terminal.py
```

## Testing machines

Local:

```
czjiang@59.66.15.239
```

Remote:

```
czjiang@166.111.25.9
```

The remote machine is only used as a testing client.
The project repository remains on the local machine.
