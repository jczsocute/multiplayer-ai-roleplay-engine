# AI RP Engine Development Guide

## Project Goal

This project implements a lightweight multiplayer AI role-playing engine.

Two players connect through terminal clients via SSH and interact in a shared AI-managed world.

The first version focuses on:
- two-player collaboration
- natural language input
- AI-managed world state
- different player perspectives
- terminal interaction

## Core Architecture

The system has three layers:

1. World Layer
- Maintains objective world state.
- Managed by World Update LLM.

2. Player View Layer
- Generates what each player knows.

3. Narration Layer
- Generates player-facing text.

The world state is the source of truth.
Chat history is a rendered record, not the world itself.

## Important Design Rules

Keep it lightweight.

Avoid:
- LangChain
- LangGraph
- AutoGen
- complex agent frameworks

Direct API calls are preferred.

AI handles:
- natural language understanding
- world state updates
- narrative generation

Python handles:
- networking
- round management
- persistence
- state transitions

## Multiplayer Model

Current supported players:
- Player A
- Player B

Each round:
1. Players edit their actions.
2. Players submit.
3. When all players submit:
   - lock the round
   - call World Update AI
   - generate player-specific responses
   - store results
4. Start next round.

## Player Status

Allowed states:

EDITING:
Player is writing.

READY:
Player submitted action.

PAUSED:
Player temporarily unavailable.

PROCESSING:
AI generation in progress.

## Storage

Use SQLite.

Do not use event sourcing.

Do not create unnecessary event models.

Store:
- current world state
- player states
- chat history
- rounds

## Coding Style

Prefer:
- simple functions
- clear names
- small modules

Avoid:
- over abstraction
- unnecessary classes

The project is a prototype.
Optimize for iteration speed.
