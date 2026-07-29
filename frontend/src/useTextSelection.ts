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
   * When false, a selection spanning more than one segment is clamped to the first.
   * The cloze passage sets this: a selection crossing a blank would otherwise ask the
   * server to gloss text that includes the hidden answer, leaking it into the popup.
   */
  allowCrossSegment?: boolean
  enabled?: boolean
}

export function useTextSelection(
  ref: React.RefObject<HTMLElement | null>,
  onPick: (picked: Picked) => void,
  { allowCrossSegment = true, enabled = true }: UseTextSelectionOptions = {},
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

    if (!allowCrossSegment && a.seg !== b.seg) {
      const seg = a.logical <= b.logical ? a.seg : b.seg
      const base = Number(seg.getAttribute(SEGMENT_ATTR))
      start = Math.max(start, base)
      end = Math.min(end, base + (seg.textContent?.length ?? 0))
    }

    const length = end - start
    if (length < MIN_CHARS || length > MAX_CHARS) return

    const rect = range.getBoundingClientRect()
    if (rect.width === 0 && rect.height === 0) return

    onPickRef.current({ text: sel.toString(), start, end, rect })
  }, [ref, allowCrossSegment])

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
