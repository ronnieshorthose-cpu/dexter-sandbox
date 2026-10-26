"""Dexter advisory AI reviewer.

Reads a pull request diff as data, optionally asks an OpenAI model for a
structured review, and posts the result as a clearly labelled advisory comment
plus a commit status pinned to the reviewed commit SHA. It never approves,
merges, deploys, or runs code from the pull request.
"""
