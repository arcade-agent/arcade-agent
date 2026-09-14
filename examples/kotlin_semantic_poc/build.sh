#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mvn -q dependency:copy-dependencies -DoutputDirectory=lib
mkdir -p build/plugin/META-INF/services build/driver
java -cp 'lib/*' org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -Werror -no-stdlib -no-reflect \
  -classpath 'lib/kotlin-stdlib-2.0.21.jar:lib/kotlin-compiler-embeddable-2.0.21.jar' \
  -d build/plugin SourceCalls.kt
printf 'arcade.poc.SourceCallsRegistrar\n' > build/plugin/META-INF/services/org.jetbrains.kotlin.compiler.plugin.CompilerPluginRegistrar
jar --create --file build/source-plugin.jar -C build/plugin .
javac --release 17 -cp 'lib/*' -d build/driver SemanticProbe.java
jar --create --file build/probe.jar -C build/driver .
