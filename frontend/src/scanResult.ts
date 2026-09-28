/**
 * Hand-off from the barcode scanner back to the Add Book screen that opened it.
 *
 * The scanner used to `router.replace` itself with a NEW Add Book screen carrying the result, so
 * every scan attempt left another Add Book (and, via its scan button, another scanner) in the stack.
 * Now the scanner stores its result here and simply goes back; Add Book takes it when it regains
 * focus. The stack is always exactly: Add Book -> Scanner, however many attempts were made.
 */
export type ScanPrefill = {
  title?: string;
  author?: string;
  cover_url?: string;
  isbn?: string;
  language?: string;
};

let pending: ScanPrefill | null = null;

export function deliverScanResult(result: ScanPrefill) {
  pending = result;
}

/** Returns the pending result once (and clears it), or null. */
export function takeScanResult(): ScanPrefill | null {
  const result = pending;
  pending = null;
  return result;
}
