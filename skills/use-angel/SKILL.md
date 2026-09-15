---
name: use-angel
description: Use when the user says "use angel" — answer from their Intersession data via angel_* tools.
version: 1.0.0
author: Intersession
license: MIT
platforms: [windows, linux, macos]
---

# Use Angel — answering from the user's own Intersession data

## Trigger

The user's message contains **"use angel"** (any casing, any position). Examples:

- `How many stories have I created? use angel`
- `use angel — what did I write about the sea?`
- `use angel list my stories from last month`

Typos of the trigger ("use angle", "use-angel", "using angel") count. Do not make the
user retype it.

## What to do

1. Strip the trigger phrase from the request; what remains is the actual question.
2. Choose exactly one tool for the primary answer:

   | The user wants | Tool |
   |---|---|
   | a count, a total, an average | `angel_stories_stats` |
   | to browse / filter / "show me" | `angel_stories_list` |
   | one specific item by id or title | `angel_stories_read` |
   | an open question about their content | `angel_stories_ask` |
   | to add something | `angel_stories_create` |
   | to remove something | `angel_stories_delete` |

3. Answer from the tool result only. Quote the number or the field you used.
4. Never estimate, never extrapolate, never answer a "how many" from memory.

## Rules

- **Never fabricate.** If the tool returns nothing, say the data set is empty — that is a
  valid, useful answer.
- **Writes need consent.** Before `angel_stories_create` or `angel_stories_delete`, restate
  what you are about to do and wait for confirmation, unless the user's message already
  named the exact action and target.
- **`angel_stories_ask` is slow on purpose.** It runs a local model on the user's own
  machine and can take up to three minutes. Say so before calling it; never retry it
  automatically on timeout.

## When no angel_* tool exists

That means NUMBERS is not connected to Intersession. Say exactly this, then answer the rest
of the question with your ordinary tools:

> NUMBERS is not connected to Intersession right now, so I can't read your stories.
> Start the Intersession server, then run `numbers connect <token>` (get a token at
> https://127.0.0.1:3000/settings under Agent Tokens).

Do not treat this as an error condition. Every other NUMBERS capability still works.
