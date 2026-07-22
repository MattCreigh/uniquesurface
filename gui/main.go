package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os/exec"
	"strconv"
)

// apiResponse is the common JSON envelope returned by every endpoint.
type apiResponse struct {
	Output  string          `json:"output,omitempty"`
	Entries []ManifestEntry `json:"entries,omitempty"`
	Error   string          `json:"error,omitempty"`
}

// runCLI executes a trinity subcommand with the given args using exec.Command
// (no shell, no string interpolation). Returns stdout and/or error.
func runCLI(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func writeJSON(w http.ResponseWriter, v apiResponse) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// handleSimple runs a trinity subcommand with the given args and returns
// its stdout.
func handleSimple(args ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		out, err := runCLI("trinity", args...)
		if err != nil {
			writeJSON(w, apiResponse{
				Output: out,
				Error:  fmt.Sprintf("trinity %s: %v", args, err),
			})
			return
		}
		writeJSON(w, apiResponse{Output: out})
	}
}

// handleCycle runs `trinity cycle` with an optional --offset flag.
// The offset value is read from the "offset" query parameter.
// If offset is negative the flag is omitted entirely (cycle to next).
func handleCycle(w http.ResponseWriter, r *http.Request) {
	args := []string{"cycle"}
	if offsetStr := r.URL.Query().Get("offset"); offsetStr != "" {
		offset, err := strconv.Atoi(offsetStr)
		if err != nil {
			writeJSON(w, apiResponse{Error: "invalid offset: " + offsetStr})
			return
		}
		if offset >= 0 {
			args = append(args, "--offset", strconv.Itoa(offset))
		}
	}
	out, err := runCLI("trinity", args...)
	if err != nil {
		writeJSON(w, apiResponse{
			Output: out,
			Error:  fmt.Sprintf("trinity cycle: %v", err),
		})
		return
	}
	writeJSON(w, apiResponse{Output: out})
}

// handleHistory reads the manifest.jsonl file and returns parsed entries.
func handleHistory(w http.ResponseWriter, r *http.Request) {
	path := DefaultManifestPath()
	entries, err := ParseManifest(path)
	if err != nil {
		writeJSON(w, apiResponse{
			Error: fmt.Sprintf("read manifest %s: %v", path, err),
		})
		return
	}
	writeJSON(w, apiResponse{Entries: entries})
}

// openBrowser attempts to open the default web browser to the given URL.
// Uses xdg-open on Linux, open on macOS, and cmd /c start on Windows.
func openBrowser(rawURL string) {
	// We don't need shell=True here — xdg-open takes a single argument.
	// Try common openers; ignore all errors.
	openers := [][]string{
		{"xdg-open", rawURL},
		{"open", rawURL},
		{"cmd", "/c", "start", "", rawURL},
	}
	for _, opener := range openers {
		_ = exec.Command(opener[0], opener[1:]...).Start()
	}
}

func main() {
	mux := http.NewServeMux()

	// API endpoints — all use POST for state-changing operations.
	mux.HandleFunc("/api/status", handleSimple("status"))
	mux.HandleFunc("/api/apply", handleSimple("apply"))
	mux.HandleFunc("/api/cycle", handleCycle)
	mux.HandleFunc("/api/restore", handleSimple("restore", "--yes"))
	mux.HandleFunc("/api/doctor", handleSimple("doctor"))
	mux.HandleFunc("/api/history", handleHistory)

	// Serve the embedded frontend.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(indexHTML)
	})

	// Bind to a random port on localhost.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatalf("failed to bind: %v", err)
	}

	addr := listener.Addr().String()
	urlStr := "http://" + addr + "/"

	log.Printf("Trinity GUI listening on %s", urlStr)

	go openBrowser(urlStr)

	if err := http.Serve(listener, mux); err != nil {
		log.Fatalf("server error: %v", err)
	}
}