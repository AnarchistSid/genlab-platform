import { describe, it, expect, vi, beforeEach } from 'vitest';
import { exportToCSV, formatKpiSummary, type CSVColumn } from '../export';

// ---------------------------------------------------------------------------
// We need to test the internal escapeCSVField function indirectly through
// exportToCSV, since it's not exported. We'll use exportToCSV and inspect
// the generated CSV content.
// ---------------------------------------------------------------------------

// Mock DOM APIs used by downloadBlob
beforeEach(() => {
  // Mock URL.createObjectURL and URL.revokeObjectURL
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:mock-url'),
    revokeObjectURL: vi.fn(),
  });
});

// Helper: capture the CSV content passed to the Blob constructor
function captureCSVContent<T>(
  data: T[],
  columns: CSVColumn<T>[],
  filename: string,
): string {
  let capturedContent = '';

  // Mock Blob to capture the content
  const origBlob = globalThis.Blob;
  globalThis.Blob = class MockBlob {
    constructor(parts: BlobPart[], _options?: BlobPropertyBag) {
      capturedContent = parts.join('');
    }
  } as unknown as typeof Blob;

  // Mock anchor element
  const mockAnchor = {
    href: '',
    download: '',
    click: vi.fn(),
  };
  vi.spyOn(document, 'createElement').mockReturnValue(mockAnchor as unknown as HTMLElement);
  vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
  vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

  exportToCSV(data, columns, filename);

  // Restore
  globalThis.Blob = origBlob;
  vi.restoreAllMocks();

  return capturedContent;
}

// ---------------------------------------------------------------------------
// CSV escaping
// ---------------------------------------------------------------------------
describe('CSV escaping via exportToCSV', () => {
  interface TestRow {
    name: string;
    value: string;
  }

  const columns: CSVColumn<TestRow>[] = [
    { key: 'name', header: 'Name' },
    { key: 'value', header: 'Value' },
  ];

  it('escapes fields containing commas by wrapping in double quotes', () => {
    const data = [{ name: 'hello, world', value: 'ok' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    // The field with a comma should be quoted
    expect(csv).toContain('"hello, world"');
  });

  it('escapes fields containing double quotes by doubling them', () => {
    const data = [{ name: 'say "hello"', value: 'ok' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    // Quotes inside should be doubled, and the field wrapped in quotes
    expect(csv).toContain('"say ""hello"""');
  });

  it('escapes fields containing newlines by wrapping in double quotes', () => {
    const data = [{ name: 'line1\nline2', value: 'ok' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    expect(csv).toContain('"line1\nline2"');
  });

  it('handles fields with both commas and quotes', () => {
    const data = [{ name: 'a "tricky", field', value: 'ok' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    // Should double the quotes AND wrap in quotes
    expect(csv).toContain('"a ""tricky"", field"');
  });

  it('sanitizes formula injection by prefixing with tab', () => {
    const data = [{ name: '=SUM(A1:A10)', value: 'ok' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    // Should be prefixed with tab to prevent formula injection
    expect(csv).toContain('\t=SUM(A1:A10)');
  });

  it('sanitizes fields starting with +, -, @', () => {
    const plusData = [{ name: '+dangerous', value: 'ok' }];
    const csv1 = captureCSVContent(plusData, columns, 'test.csv');
    expect(csv1).toContain('\t+dangerous');

    const minusData = [{ name: '-cmd', value: 'ok' }];
    const csv2 = captureCSVContent(minusData, columns, 'test.csv');
    expect(csv2).toContain('\t-cmd');

    const atData = [{ name: '@malicious', value: 'ok' }];
    const csv3 = captureCSVContent(atData, columns, 'test.csv');
    expect(csv3).toContain('\t@malicious');
  });

  it('leaves simple alphanumeric fields unquoted', () => {
    const data = [{ name: 'simple', value: 'text' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    // Lines should just be: Name,Value\r\nsimple,text
    const lines = csv.replace('\uFEFF', '').split('\r\n');
    expect(lines[0]).toBe('Name,Value');
    expect(lines[1]).toBe('simple,text');
  });

  it('handles null/undefined values as empty strings', () => {
    const data = [{ name: null as unknown as string, value: undefined as unknown as string }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    const lines = csv.replace('\uFEFF', '').split('\r\n');
    expect(lines[1]).toBe(',');
  });
});

// ---------------------------------------------------------------------------
// BOM inclusion
// ---------------------------------------------------------------------------
describe('CSV BOM (Byte Order Mark)', () => {
  it('prepends UTF-8 BOM for Excel compatibility', () => {
    const data = [{ x: 'a' }];
    const columns: CSVColumn<{ x: string }>[] = [{ key: 'x', header: 'X' }];
    const csv = captureCSVContent(data, columns, 'test.csv');
    expect(csv.charCodeAt(0)).toBe(0xfeff);
  });
});

// ---------------------------------------------------------------------------
// Row structure
// ---------------------------------------------------------------------------
describe('CSV row structure', () => {
  it('generates correct header and data rows', () => {
    interface Metric {
      channel: string;
      views: number;
      published: string;
    }
    const columns: CSVColumn<Metric>[] = [
      { key: 'channel', header: 'Channel' },
      { key: 'views', header: 'Views' },
      { key: 'published', header: 'Published' },
    ];
    const data: Metric[] = [
      { channel: 'CriticalRush', views: 1500, published: '2026-03-20' },
      { channel: 'ClutchWire', views: 3200, published: '2026-03-21' },
    ];
    const csv = captureCSVContent(data, columns, 'export.csv');
    const lines = csv.replace('\uFEFF', '').split('\r\n');
    expect(lines[0]).toBe('Channel,Views,Published');
    expect(lines[1]).toBe('CriticalRush,1500,2026-03-20');
    expect(lines[2]).toBe('ClutchWire,3200,2026-03-21');
  });

  it('handles empty data array', () => {
    const columns: CSVColumn<{ x: string }>[] = [{ key: 'x', header: 'X' }];
    const csv = captureCSVContent([], columns, 'empty.csv');
    const lines = csv.replace('\uFEFF', '').split('\r\n');
    // Should only have the header row
    expect(lines[0]).toBe('X');
    expect(lines.length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// formatKpiSummary
// ---------------------------------------------------------------------------
describe('formatKpiSummary', () => {
  it('formats small numbers without suffix', () => {
    const result = formatKpiSummary(500, 200, '3 reels');
    expect(result).toBe('500 reach · 200 likes · 3 reels published');
  });

  it('formats large numbers with K suffix', () => {
    const result = formatKpiSummary(12300, 4500, '7 reels');
    expect(result).toBe('12.3K reach · 4.5K likes · 7 reels published');
  });

  it('formats exact thousands', () => {
    const result = formatKpiSummary(1000, 1000, '1 reel');
    expect(result).toBe('1.0K reach · 1.0K likes · 1 reel published');
  });
});
