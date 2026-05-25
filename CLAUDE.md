# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

fee-check-agent routes Claude Code through DeepSeek's Anthropic-compatible API endpoint (`api.deepseek.com/anthropic`), using the `deepseek-chat` model.

## Project Structure

- `start_claude.ps1` — Entry point; sets `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, and model overrides before launching `claude`. **This file contains a plaintext API key — do not commit it to version control.**
- `cache/` — Agent operation cache (populated at runtime)
- `input/` — Input data files for the agent's data pipeline
- `output/` — Output/results from agent execution
- `prompts/` — Prompt templates used by the agent
- `.venv/` — Python 3.14 virtual environment

## Commands

- **Launch agent**: `.\start_claude.ps1` (from PowerShell)
- **Activate Python venv**: `.\.venv\Scripts\Activate.ps1`

## Data Pipeline

The agent's data pipeline uses four directories: `input/` for source data, `prompts/` for templates, `cache/` for intermediate results, and `output/` for final results. All start empty and are populated during agent execution.
