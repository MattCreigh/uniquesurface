package main

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
)

// ManifestEntry represents a single line from manifest.jsonl.
// Unknown fields are silently ignored by encoding/json.
type ManifestEntry struct {
	Ts            string `json:"ts"`
	Op            string `json:"op"`
	Path          string `json:"path"`
	PrevSHA256    string `json:"prev_sha256"`
	NewSHA256     string `json:"new_sha256"`
	PrevBytesPath string `json:"prev_bytes_path"`
}

// ParseManifest reads a JSONL manifest file and returns parsed entries.
// Corrupt or empty lines are silently skipped, mirroring the Python
// implementation's tolerant behaviour.
func ParseManifest(path string) ([]ManifestEntry, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var entries []ManifestEntry
	scanner := bufio.NewScanner(f)
	// Allow lines up to 1 MiB (manifest entries can contain long paths).
	scanner.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var entry ManifestEntry
		if err := json.Unmarshal(line, &entry); err != nil {
			continue // skip corrupt lines
		}
		entries = append(entries, entry)
	}
	return entries, scanner.Err()
}

// DefaultManifestPath returns the expected location of manifest.jsonl
// under the user's home directory.
func DefaultManifestPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "state", "trinity", "manifest.jsonl")
}
