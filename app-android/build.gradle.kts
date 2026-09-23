// Kotlin 2.0부터 Compose 컴파일러가 별도 플러그인으로 분리됐다.
// composeOptions.kotlinCompilerExtensionVersion 은 더 이상 쓰지 않는다.
//
// 버전은 이 PC의 Gradle 캐시에 이미 받아져 있는 것으로 맞췄다(다운로드 최소화).
plugins {
    id("com.android.application") version "8.13.1" apply false
    id("org.jetbrains.kotlin.android") version "2.1.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.20" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.20" apply false
}
