package main

import (
	"os"
	"path/filepath"
	"testing"
)

// helper: write content to a temp file and return its path.
func writeTempFile(t *testing.T, name, content string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(content), 0644); err != nil {
		t.Fatalf("write temp file: %v", err)
	}
	return p
}

func TestParseManifest_ValidEntries(t *testing.T) {
	data := `{"ts":"2025-01-01T00:00:00Z","op":"apply","path":"/wallpapers/a.jpg","prev_sha256":"abc","new_sha256":"def","prev_bytes_path":"/tmp/prev_a"}
{"ts":"2025-01-01T01:00:00Z","op":"cycle","path":"/wallpapers/b.jpg","prev_sha256":"ghi","new_sha256":"jkl","prev_bytes_path":"/tmp/prev_b"}
`
	p := writeTempFile(t, "manifest.jsonl", data)

	entries, err := ParseManifest(p)
	if err != nil {
		t.Fatalf("ParseManifest error: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(entries))
	}

	e0 := entries[0]
	if e0.Ts != "2025-01-01T00:00:00Z" {
		t.Errorf("entry 0 ts: got %q", e0.Ts)
	}
	if e0.Op != "apply" {
		t.Errorf("entry 0 op: got %q", e0.Op)
	}
	if e0.Path != "/wallpapers/a.jpg" {
		t.Errorf("entry 0 path: got %q", e0.Path)
	}
	if e0.PrevSHA256 != "abc" {
		t.Errorf("entry 0 prev_sha256: got %q", e0.PrevSHA256)
	}
	if e0.NewSHA256 != "def" {
		t.Errorf("entry 0 new_sha256: got %q", e0.NewSHA256)
	}
	if e0.PrevBytesPath != "/tmp/prev_a" {
		t.Errorf("entry 0 prev_bytes_path: got %q", e0.PrevBytesPath)
	}

	if entries[1].Op != "cycle" {
		t.Errorf("entry 1 op: got %q", entries[1].Op)
	}
}

func TestParseManifest_SkipsEmptyAndCorruptLines(t *testing.T) {
	data := `{"ts":"2025-01-01T00:00:00Z","op":"apply","path":"/w/a.jpg","prev_sha256":"","new_sha256":"","prev_bytes_path":""}

{not valid json
{"ts":"2025-01-01T02:00:00Z","op":"restore","path":"/w/c.jpg","prev_sha256":"x","new_sha256":"y","prev_bytes_path":"/tmp/prev_c"}
`
	p := writeTempFile(t, "manifest.jsonl", data)

	entries, err := ParseManifest(p)
	if err != nil {
		t.Fatalf("ParseManifest error: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("expected 2 valid entries (empty + corrupt skipped), got %d", len(entries))
	}
	if entries[0].Op != "apply" {
		t.Errorf("entry 0 op: got %q", entries[0].Op)
	}
	if entries[1].Op != "restore" {
		t.Errorf("entry 1 op: got %q", entries[1].Op)
	}
}

func TestParseManifest_MissingFile(t *testing.T) {
	_, err := ParseManifest(filepath.Join(t.TempDir(), "does_not_exist.jsonl"))
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
	if !os.IsNotExist(err) {
		t.Fatalf("expected os.IsNotExist error, got: %v", err)
	}
}

func TestParseManifest_EmptyFile(t *testing.T) {
	p := writeTempFile(t, "empty.jsonl", "")
	entries, err := ParseManifest(p)
	if err != nil {
		t.Fatalf("ParseManifest error: %v", err)
	}
	if entries != nil {
		t.Fatalf("expected nil entries for empty file, got %d", len(entries))
	}
}

func TestDefaultManifestPath(t *testing.T) {
	p := DefaultManifestPath()
	if p == "" {
		t.Fatal("DefaultManifestPath returned empty string")
	}
	// Should end with the expected suffix.
	if !filepath.IsAbs(p) {
		t.Errorf("expected absolute path, got %q", p)
	}
}
