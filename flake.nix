{
  description = "Trinity — unified Plasma 6 surface set manager (desktop, lock, login wallpapers).";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Single source of truth for the version is src/trinity/__init__.py
      # (hatch reads it there too — pyproject declares version as dynamic).
      version =
        builtins.head
          (builtins.match ".*__version__ = \"([^\"]+)\".*"
            (builtins.readFile ./src/trinity/__init__.py));
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "trinity";
          inherit version;
          pyproject = true;
          src = ./.;

          build-system = [ pkgs.python3Packages.hatchling ];

          dependencies = with pkgs.python3Packages; [
            pydantic
            httpx
            pillow
            platformdirs
            structlog
            pluggy
            click
            packaging
            defusedxml
          ];

          nativeCheckInputs = with pkgs.python3Packages; [
            pytestCheckHook
            respx
            hypothesis
          ];

          pythonImportsCheck = [ "trinity" ];

          meta = {
            description = "Unified Plasma 6 surface set manager (desktop, lock, login wallpapers)";
            homepage = "https://github.com/MattCreigh/trinity_background_manager";
            license = pkgs.lib.licenses.gpl3Plus;
            mainProgram = "trinity";
          };
        };
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = pkgs.lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };
      });

      checks = forAllSystems (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      devShells = forAllSystems (pkgs: {
        # uv drives the day-to-day workflow (uv sync/run, groups from
        # pyproject); the shell just supplies the interpreter and tools
        # that are not pip-installable: qmllint6 for the post-patch
        # lint gate (qt6.qtdeclarative) would pull most of Qt, so it is
        # left to the host system.
        default = pkgs.mkShell {
          packages = [
            pkgs.python312
            pkgs.uv
          ];
          env.UV_PYTHON_DOWNLOADS = "never";
        };
      });
    };
}
