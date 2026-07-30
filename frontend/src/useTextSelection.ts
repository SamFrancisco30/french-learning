import { useCallback, useEffect, useRef } from 'react'

/**
 * Resolve a mouse/keyboard text selection to character offsets in a passage.
 *
 * The hard part is that a passage is not one text node. The cloze exercise renders it as
 * interleaved `<span>` and `<input>` siblings, and the transcript container also holds a
 * heading element. Walking `textContent` would therefore produce offsets that silently
 * disagree with the string the backend has — and a wrong offset means confidently glossing
 * the wrong word, which is worse than glossing nothing.
 *
 * So offsets are not inferred, they are *declared*: every text chunk is rendered as
 * `<span data-off="N">`, where N is its start offset in the logical passage. Resolving a
 * DOM position is then a walk up to the nearest `[data-off]` ancestor plus the offset
 * inside it. Anything without such an ancestor — an `<input>`, a decoration, a label — is
 * outside the passage by construction and is ignored.
 */

export interface Picked {
  text: string
  start: number
  end: number
  /** Viewport rect of the selection, for anchoring the popup. */
  rect: DOMRect
}

const MIN_CHARS = 1
// Long enough for a full sentence — the grammar feature needs whole clauses, not just
// words. The backend caps sentence analysis at 600 chars independently.
const MAX_CHARS = 400

const SEGMENT_ATTR = 'data-off'

function segmentOf(node: Node | null, root: HTMLElement): HTMLElement | null {
  let el: HTMLElement | null =
    node == null
      ? null
      : node.nodeType === Node.TEXT_NODE
        ? node.parentElement
        : (node as HTMLElement)
  while (el && el !== root.parentElement) {
    if (el.hasAttribute?.(SEGMENT_ATTR)) return el
    if (el === root) return null
    el = el.parentElement
  }
  return null
}

/** Character offset of (node, offset) within its own segment element. */
function offsetWithinSegment(seg: HTMLElement, node: Node, offset: number): number {
  if (node === seg) {
    // Offset is a child index, not a character index.
    let total = 0
    for (let i = 0; i < offset && i < seg.childNodes.length; i++) {
      total += seg.childNodes[i].textContent?.length ?? 0
    }
    return total
  }
  let total = 0
  for (const child of Array.from(seg.childNodes)) {
    if (child === node || child.contains?.(node)) {
      return total + (child === node ? offset : 0)
    }
    total += child.textContent?.length ?? 0
  }
  return total
}

function resolvePoint(
  node: Node,
  offset: number,
  root: HTMLElement,
): { seg: HTMLElement; logical: number } | null {
  const seg = segmentOf(node, root)
  if (!seg) return null
  const base = Number(seg.getAttribute(SEGMENT_ATTR))
  if (!Number.isFinite(base)) return null
  return { seg, logical: base + offsetWithinSegment(seg, node, offset) }
}

export interface UseTextSelectionOptions {
  /**
   * Character ranges the resolved selection may never touch. The cloze passage passes its
   * blanks: a selection dragged across one would otherwise ask the server to gloss a range
   * that includes the hidden answer, printing it into the popup.
   *
   * This replaced a "clamp to the first segment" rule, which happened to work only because a
   * cloze chunk between two blanks was a single segment. Once the passage is split per word —
   * which follow-along highlighting requires — that rule would clamp every phrase selection
   * down to one word and take the sentence-grammar feature with it. Naming the real
   * constraint fixes both: whole sentences select freely, blanks stay sealed.
   */
  blockedRanges?: number[][]
  enabled?: boolean
}

/**
 * Largest part of [start, end) that lies wholly between blocked ranges.
 *
 * Returning the biggest safe portion rather than nothing keeps a slightly-too-greedy drag
 * useful — you get the phrase you meant, minus the blank — and the result is safe by
 * construction because it sits inside a single gap.
 */
function clampToSafeGap(
  start: number,
  end: number,
  blocked: number[][],
): [number, number] | null {
  const sorted = [...blocked]
    .map(([s, e]) => [Math.min(s, e), Math.max(s, e)] as [number, number])
    .sort((a, b) => a[0] - b[0])

  const gaps: [number, number][] = []
  let cursor = 0
  for (const [bs, be] of sorted) {
    if (bs > cursor) gaps.push([cursor, bs])
    cursor = Math.max(cursor, be)
  }
  gaps.push([cursor, Number.MAX_SAFE_INTEGER])

  let best: [number, number] | null = null
  let bestLen = 0
  for (const [gs, ge] of gaps) {
    const s = Math.max(start, gs)
    const e = Math.min(end, ge)
    if (e - s > bestLen) {
      bestLen = e - s
      best = [s, e]
    }
  }
  return best
}

export function useTextSelection(
  ref: React.RefObject<HTMLElement | null>,
  onPick: (picked: Picked) => void,
  { blockedRanges, enabled = true }: UseTextSelectionOptions = {},
) {
  // Keep the callback in a ref so listeners don't re-bind on every render.
  const onPickRef = useRef(onPick)
  useEffect(() => {
    onPickRef.current = onPick
  }, [onPick])

  const evaluate = useCallback(() => {
    const root = ref.current
    if (!root) return

    // A focused input owns its own selection; never treat typing as a lookup.
    const active = document.activeElement
    if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return

    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return

    const range = sel.getRangeAt(0)
    if (!root.contains(range.commonAncestorContainer)) return

    const a = resolvePoint(range.startContainer, range.startOffset, root)
    const b = resolvePoint(range.endContainer, range.endOffset, root)
    if (!a || !b) return

    let start = Math.min(a.logical, b.logical)
    let end = Math.max(a.logical, b.logical)

    if (blockedRanges && blockedRanges.length > 0) {
      const safe = clampToSafeGap(start, end, blockedRanges)
      if (!safe) return
      start = safe[0]
      end = safe[1]
    }

    const length = end - start
    if (length < MIN_CHARS || length > MAX_CHARS) return

    const rect = range.getBoundingClientRect()
    if (rect.width === 0 && rect.height === 0) return

    onPickRef.current({ text: sel.toString(), start, end, rect })
  }, [ref, blockedRanges])

  useEffect(() => {
    if (!enabled) return
    const root = ref.current
    if (!root) return

    // mouseup / keyup rather than selectionchange: we want the finished selection, not
    // every intermediate state during a drag.
    const onMouseUp = () => window.setTimeout(evaluate, 0)
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.shiftKey || e.key === 'Shift') window.setTimeout(evaluate, 0)
    }

    root.addEventListener('mouseup', onMouseUp)
    root.addEventListener('keyup', onKeyUp)
    return () => {
      root.removeEventListener('mouseup', onMouseUp)
      root.removeEventListener('keyup', onKeyUp)
    }
  }, [ref, evaluate, enabled])
}
