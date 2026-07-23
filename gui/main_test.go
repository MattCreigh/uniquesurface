package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHostValidationMiddleware(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	middleware := hostValidationMiddleware("8080", handler)

	tests := []struct {
		name       string
		host       string
		wantStatus int
	}{
		{"Valid 127.0.0.1", "127.0.0.1:8080", http.StatusOK},
		{"Valid localhost", "localhost:8080", http.StatusOK},
		{"Invalid Host IP", "192.168.1.1:8080", http.StatusMisdirectedRequest},
		{"Invalid Host name", "evil.com:8080", http.StatusMisdirectedRequest},
		{"No port", "localhost", http.StatusMisdirectedRequest},
		{"Wrong port", "localhost:9090", http.StatusMisdirectedRequest},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("GET", "http://"+tt.host+"/", nil)
			req.Host = tt.host
			rec := httptest.NewRecorder()
			middleware.ServeHTTP(rec, req)
			if rec.Code != tt.wantStatus {
				t.Errorf("host %q: expected status %d, got %d", tt.host, tt.wantStatus, rec.Code)
			}
		})
	}
}

func TestTokenValidationMiddleware(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	token := "secret-token"
	middleware := tokenValidationMiddleware(token, handler)

	tests := []struct {
		name       string
		path       string
		authHeader string
		wantStatus int
	}{
		{"Non-API path bypasses token check", "/", "", http.StatusOK},
		{"API path with valid token", "/api/status", "Bearer secret-token", http.StatusOK},
		{"API path with invalid token", "/api/status", "Bearer wrong-token", http.StatusUnauthorized},
		{"API path with missing token", "/api/status", "", http.StatusUnauthorized},
		{"API path with malformed header", "/api/status", "Bearer-secret-token", http.StatusUnauthorized},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("POST", "http://localhost:8080"+tt.path, nil)
			if tt.authHeader != "" {
				req.Header.Set("Authorization", tt.authHeader)
			}
			rec := httptest.NewRecorder()
			middleware.ServeHTTP(rec, req)
			if rec.Code != tt.wantStatus {
				t.Errorf("path %q, auth %q: expected status %d, got %d", tt.path, tt.authHeader, tt.wantStatus, rec.Code)
			}
		})
	}
}

func TestStateChangingMethods(t *testing.T) {
	tests := []struct {
		name       string
		handler    http.HandlerFunc
		method     string
		wantStatus int
	}{
		{"GET restore is 405", handleSimple("restore", "--yes"), "GET", http.StatusMethodNotAllowed},
		{"GET cycle is 405", handleCycle, "GET", http.StatusMethodNotAllowed},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(tt.method, "/api/endpoint", nil)
			rec := httptest.NewRecorder()
			tt.handler.ServeHTTP(rec, req)
			if rec.Code != tt.wantStatus {
				t.Errorf("expected status %d, got %d", tt.wantStatus, rec.Code)
			}
		})
	}
}
