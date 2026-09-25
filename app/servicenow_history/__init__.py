"""POINT 19 (Task #19): ServiceNow historical incidents as an RCA precedent source.

This package PULLS past (resolved/closed) ServiceNow incidents and makes them
available to the RCA investigator as precedent ("we've seen this before — here's
how it was fixed"). It is the READ counterpart to app/sinks/servicenow_sink.py
(which WRITES tickets).

Modules:
  client.py     — fetch resolved incidents from the ServiceNow REST API
  loader.py     — upsert fetched incidents into the historical_incident table
  retrieval.py  — given a live incident, find the most similar past incidents
"""
