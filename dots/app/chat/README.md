# Chat

Terminal-native chat UI.

The chat UI owns prompt handling, streaming display, slash commands, and
pool-ready status presentation. It talks to `Worker` as a client API and
does not import backend, scheduler, or wire-protocol details.
