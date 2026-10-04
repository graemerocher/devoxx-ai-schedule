# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Runtime image for the Pyronaut fat JAR. Build the JAR first with
# `pyronaut build --jar` (or `just build`). The embedded GraalPy runtime needs a
# GraalVM JDK so that Python code is JIT-compiled.
FROM container-registry.oracle.com/graalvm/jdk:25
WORKDIR /app

COPY dist/dvxaisched-0.1.0.jar /app/application.jar

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/application.jar"]
