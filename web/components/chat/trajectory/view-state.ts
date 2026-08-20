/**
 * The session page has two peer views — the conversation and the trajectory —
 * and switching between them must leave the conversation exactly as it was.
 *
 * The rules below are separated from the page so they can be checked without a
 * browser (`web/tests/trajectory-model.test.ts`).
 */

export type SessionView = "conversation" | "trajectory";

/**
 * Extra classes appended to the transcript's scroll container while the other
 * view is on screen.
 *
 * The hidden state is `visibility: hidden` over the same box (`absolute
 * inset-0` of the positioned wrapper it already lives in), **never**
 * `display: none`, and the subtree is never unmounted. Three concrete things
 * break under `display: none`, all of them in `useChatAutoScroll`:
 *
 *  1. the layout-phase pin assigns `container.scrollTop = container.scrollHeight`
 *     (`hooks/useChatAutoScroll.ts:69`) and a display-none subtree reports
 *     `scrollHeight === 0`, so a turn that streams while the trajectory is open
 *     would silently rewind the transcript to the top;
 *  2. the same pin is what keeps a live turn glued to the bottom, so follow
 *     state would be lost rather than merely paused;
 *  3. the scroll offset itself is not guaranteed to survive the box being
 *     destroyed and rebuilt.
 *
 * `visibility: hidden` keeps the box, its metrics and its scroll offset while
 * removing it from paint, from hit testing and from the tab order.
 */
export function conversationLayerClass(view: SessionView): string {
  return view === "trajectory"
    ? "pointer-events-none invisible absolute inset-0"
    : "";
}

/**
 * Whether the turn navigator may render.
 *
 * It binds `Alt+ArrowUp/Down` on `window` and only steps aside for input
 * controls (`TurnNavigator.tsx:201-222`), so leaving it mounted behind the
 * trajectory would let an invisible list keep eating the arrow keys the
 * trajectory's own rows need (A32).
 */
export function shouldRenderTurnNavigator(view: SessionView): boolean {
  return view === "conversation";
}
