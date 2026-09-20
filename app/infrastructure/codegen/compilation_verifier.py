import os
import platform
import subprocess
from pathlib import Path
from typing import Tuple


class CompilationError(Exception):
    def __init__(self, message: str, stdout: str, stderr: str):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CompilationVerifier:
    """Verifies that a generated Spring Boot Maven project compiles, passes tests, and packages cleanly."""

    @classmethod
    def verify(cls, project_dir: Path, timeout_seconds: int = 600) -> Tuple[bool, str]:
        project_dir = Path(project_dir).resolve()
        is_windows = platform.system() == "Windows"
        wrapper_file = project_dir / ("mvnw.cmd" if is_windows else "mvnw")

        if not wrapper_file.exists():
            raise FileNotFoundError(f"Maven wrapper not found at '{wrapper_file}'.")

        # Ensure JAVA_HOME points to Java 21
        env = os.environ.copy()
        if "JAVA_HOME" not in env or "jdk-21" not in env.get("JAVA_HOME", ""):
            adoptium_path = Path("C:/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot")
            if adoptium_path.exists():
                env["JAVA_HOME"] = str(adoptium_path)
                env["PATH"] = f"{str(adoptium_path / 'bin')};{env.get('PATH', '')}"

        # 1. Execute 'mvnw test'
        if is_windows:
            test_cmd = ["cmd.exe", "/c", "mvnw.cmd", "clean", "test"]
        else:
            test_cmd = ["./mvnw", "clean", "test"]

        proc_test = subprocess.run(
            test_cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
            env=env,
        )

        if proc_test.returncode != 0:
            raise CompilationError(
                f"Maven test execution failed with exit code {proc_test.returncode}.",
                stdout=proc_test.stdout,
                stderr=proc_test.stderr,
            )

        # 2. Execute 'mvnw package'
        if is_windows:
            pkg_cmd = ["cmd.exe", "/c", "mvnw.cmd", "package"]
        else:
            pkg_cmd = ["./mvnw", "package"]

        proc_pkg = subprocess.run(
            pkg_cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
            env=env,
        )

        if proc_pkg.returncode != 0:
            raise CompilationError(
                f"Maven package execution failed with exit code {proc_pkg.returncode}.",
                stdout=proc_pkg.stdout,
                stderr=proc_pkg.stderr,
            )

        return True, "BUILD SUCCESS: Tests passed and artifact packaged successfully."

