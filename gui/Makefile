.PHONY: build test clean run

build:
	go build -o trinity-gui .

test:
	go test ./...

clean:
	rm -f trinity-gui

run: build
	./trinity-gui