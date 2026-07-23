package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"

	webview "github.com/webview/webview_go"
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
		// Enforce POST on state-changing operations
		subcmd := args[0]
		if (subcmd == "apply" || subcmd == "restore") && r.Method != http.MethodPost {
			http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
			return
		}
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
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}
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

func hostValidationMiddleware(allowedPort string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		allowed1 := "127.0.0.1:" + allowedPort
		allowed2 := "localhost:" + allowedPort
		if r.Host != allowed1 && r.Host != allowed2 {
			http.Error(w, "Disallowed Host Header", http.StatusMisdirectedRequest) // 421
			return
		}
		next.ServeHTTP(w, r)
	})
}

func tokenValidationMiddleware(token string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") {
			auth := r.Header.Get("Authorization")
			expected := "Bearer " + token
			if auth != expected {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusUnauthorized)
				_ = json.NewEncoder(w).Encode(apiResponse{Error: "Unauthorized (invalid or missing bearer token)"})
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	// The native WebKitGTK window must own the main OS thread (GTK
	// requirement), so pin the main goroutine to it.
	runtime.LockOSThread()

	// Generate random 16-byte hex token for bearer auth
	tokenBytes := make([]byte, 16)
	if _, err := rand.Read(tokenBytes); err != nil {
		log.Fatalf("failed to generate token: %v", err)
	}
	bearerToken := hex.EncodeToString(tokenBytes)

	mux := http.NewServeMux()

	// API endpoints
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
		html := string(indexHTML)
		tokenScript := fmt.Sprintf("<script>window.bearerToken = %q;</script>", bearerToken)
		html = strings.Replace(html, "<head>", "<head>"+tokenScript, 1)

		targetFetch := "const resp = await fetch(endpoint, { method: 'POST' });"
		replacedFetch := "const resp = await fetch(endpoint, { method: 'POST', headers: { 'Authorization': 'Bearer ' + window.bearerToken } });"
		html = strings.Replace(html, targetFetch, replacedFetch, 1)

		_, _ = w.Write([]byte(html))
	})

	// Bind to a random port on localhost.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatalf("failed to bind: %v", err)
	}

	_, port, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		log.Fatalf("failed to split host/port: %v", err)
	}

	addr := listener.Addr().String()
	urlStr := "http://" + addr + "/"

	log.Printf("Trinity GUI Bearer Token: %s", bearerToken)
	log.Printf("Trinity GUI listening on %s", urlStr)

	server := &http.Server{
		Handler:           hostValidationMiddleware(port, tokenValidationMiddleware(bearerToken, mux)),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// Serve in the background; the native window owns the main thread.
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			log.Fatalf("server error: %v", err)
		}
	}()

	// Render the UI in a native WebKitGTK window — no external browser.
	// If a window cannot be created (e.g. a headless/SSH session with no
	// display), fall back to the system browser so the GUI stays reachable.
	if !openWebview(urlStr) {
		log.Printf("no native display available; falling back to the system browser")
		openBrowser(urlStr)
		select {} // keep serving until the process is killed
	}
}

// openWebview renders the GUI in a native WebKitGTK window. It returns
// false if a window could not be created (e.g. no display), so the caller
// can fall back to a browser. The window owns the calling (main) thread
// until the user closes it, at which point the process exits.
func openWebview(rawURL string) (ok bool) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("native window unavailable: %v", r)
			ok = false
		}
	}()
	w := webview.New(false)
	if w == nil {
		return false
	}
	defer w.Destroy()
	w.SetTitle("Trinity Wallpaper Manager")
	w.SetSize(920, 720, webview.HintNone)
	w.Navigate(rawURL)
	w.Run()
	return true
}
