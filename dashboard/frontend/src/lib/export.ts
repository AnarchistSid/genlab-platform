/**
 * Client-side data export utilities.
 *
 * Supports CSV (with Excel-compatible UTF-8 BOM) and JSON downloads
 * via temporary anchor elements and Blob URLs.
 */

/**
 * Escape a CSV field value.
 * Wraps in double quotes if the value contains commas, quotes, or newlines.
 */
function escapeCSVField(value: unknown): string {
  const str = value == null ? "" : String(value);
  // Sanitize formula injection: prefix dangerous leading chars with a tab
  // Prevents =, +, -, @, \t, \r from being interpreted as formulas in Excel
  const sanitized = /^[=+\-@\t\r]/.test(str) ? `\t${str}` : str;
  // If the field contains a comma, double-quote, or newline, wrap it in quotes
  if (sanitized.includes(",") || sanitized.includes('"') || sanitized.includes("\n") || sanitized.includes("\r")) {
    return `"${sanitized.replace(/"/g, '""')}"`;
  }
  return sanitized;
}

/**
 * Trigger a browser download for a string payload.
 */
function downloadBlob(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  // Clean up
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export interface CSVColumn<T> {
  key: keyof T;
  header: string;
}

/**
 * Export an array of objects as a CSV file and trigger browser download.
 *
 * - Handles special characters (commas, quotes, newlines) via quoting
 * - Prepends UTF-8 BOM (\uFEFF) for Excel compatibility
 *
 * @param data     - Array of row objects
 * @param columns  - Column definitions (key + header label)
 * @param filename - Download filename (should end in .csv)
 */
export function exportToCSV<T>(
  data: T[],
  columns: Array<CSVColumn<T>>,
  filename: string,
): void {
  // Header row
  const headerRow = columns.map((col) => escapeCSVField(col.header)).join(",");

  // Data rows
  const dataRows = data.map((row) =>
    columns.map((col) => escapeCSVField(row[col.key as keyof T])).join(","),
  );

  // UTF-8 BOM + CSV content
  const csv = "\uFEFF" + [headerRow, ...dataRows].join("\r\n");
  downloadBlob(csv, filename, "text/csv;charset=utf-8;");
}

/**
 * Export arbitrary data as a JSON file and trigger browser download.
 *
 * @param data     - Any JSON-serializable value
 * @param filename - Download filename (should end in .json)
 */
export function exportToJSON(data: unknown, filename: string): void {
  const json = JSON.stringify(data, null, 2);
  downloadBlob(json, filename, "application/json;charset=utf-8;");
}

/**
 * Copy a string to the clipboard via the Clipboard API.
 */
export function copyToClipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text);
}

/**
 * Format a KPI summary string for display or copying.
 * e.g. "12.3K reach · 4.5K likes · 7 published"
 */
export function formatKpiSummary(reach: number, likes: number, published: string): string {
  const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
  return `${fmt(reach)} reach · ${fmt(likes)} likes · ${published} published`;
}
