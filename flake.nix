{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/26.05";
  };

  outputs =
  { nixpkgs, ... }:
  let
    lib = nixpkgs.lib;
    
    forAllSystems = nixpkgs.lib.genAttrs nixpkgs.lib.systems.flakeExposed;
  in
  {
    devShells = forAllSystems (
      system:
      let
        pkgs = import nixpkgs { inherit system; };

        pythonEnv = pkgs.python314.withPackages (
          ps: [
            ps.python-gitlab
            ps.requests
            ps.python-dateutil
            (ps.callPackage ./nix/pyforgejo.nix {})
            ps.docopt-ng
            ps.click
            ps.pyyaml

            # lsp
            ps.ruff
          ]
        );
      in
      {
        default = pkgs.mkShell {
          packages = [
            pythonEnv
          ];
        };
      }
    ); # END devShells
  };
}
