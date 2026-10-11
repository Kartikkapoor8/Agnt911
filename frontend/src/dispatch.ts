// Every typed event from /state.events goes through onEvent exactly once, in stream order.
// useWorldState finds the events that are new since the last poll and calls onEvent for each;
// the story feed gets every one, and animation handlers (map markers, fleet chips) get the live ones.

import { pushStory, resetStory } from "./story";
import type { TypedEvent } from "./types";

export interface EventMeta {
  runId: string;
  backfill: boolean; // already in the stream when this page first connected: show it in the feed, don't animate it
}

type Listener = (event: TypedEvent, meta: EventMeta) => void;

const animators = new Set<Listener>();

/** Run fn for every live event (not backfill), for one-shot animations. Returns an unsubscribe function. */
export function onLiveEvent(fn: Listener): () => void {
  animators.add(fn);
  return () => animators.delete(fn);
}

/** The single event dispatcher: story first, then any animations. */
export function onEvent(event: TypedEvent, meta: EventMeta): void {
  pushStory(event);
  if (!meta.backfill) animators.forEach((fn) => fn(event, meta));
}

const resetters = new Set<(runId: string) => void>();

/** Run fn whenever a new run starts. Returns an unsubscribe function. */
export function onRunReset(fn: (runId: string) => void): () => void {
  resetters.add(fn);
  return () => resetters.delete(fn);
}

/** A new run started (first connect or POST /mode): drop everything keyed to the old one. */
export function onRunChange(runId: string): void {
  resetStory(runId);
  resetters.forEach((fn) => fn(runId));
}

const OVERLAP = 4; // how many trailing events must match to place the previous window inside the new one

/**
 * The events in `next` that were not in `prev`. Both are windows (oldest first, at most 500) of the
 * same append-only stream, and events carry no id, so the previous window's last few events are found
 * in the new one by value and everything after them is new.
 */
export function newEvents(prev: TypedEvent[], next: TypedEvent[]): TypedEvent[] {
  if (prev.length === 0) return next;
  const m = Math.min(OVERLAP, prev.length);
  const tail = prev.slice(-m).map((e) => JSON.stringify(e));
  const keys = new Map<number, string>();
  const key = (i: number) => {
    let k = keys.get(i);
    if (k === undefined) keys.set(i, (k = JSON.stringify(next[i])));
    return k;
  };
  for (let end = next.length - 1; end >= m - 1; end--) {
    let match = true;
    for (let k = 0; k < m && match; k++) match = key(end - m + 1 + k) === tail[k];
    if (match) return next.slice(end + 1);
  }
  return next; // no overlap: a whole window went by between polls, so all of it is new
}
