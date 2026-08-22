{
  lib,
  buildPythonPackage,
  fetchPypi,
  # Build-system
  uv,
  setuptools,
  # Dependencies
  httpx,
  pydantic,
  pydantic-core,
  python-dotenv,
}:

buildPythonPackage rec {
  pname = "pyforgejo";
  version = "2.0.7";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-gFHXul8ANHfv/3pzrgKGo4wy4+CB2v9NgblCb8B6Cz8=";
  };

  patches = [ ./remove_poetry.patch ];

  build-system = [ setuptools uv ];

  dependencies = [
    httpx
    pydantic
    pydantic-core
    python-dotenv
  ];

  pythonImportsCheck = [ pname ];

  meta = {
    description = "A Python client library for accessing the Forgejo API.";
    homepage = "https://codeberg.org/harabat/pyforgejo";
    license = lib.licenses.mit;
    maintainers = with lib.maintainers; [ hugomartel ];
  };
}
