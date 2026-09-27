package com.petcare.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * 서버 통신.
 *
 * Retrofit/OkHttp를 쓰지 않는다. 호출이 세 개뿐이라 라이브러리를 얹을 이유가 없고,
 * 의존성이 늘면 빌드가 깨질 여지만 커진다. (게이트웨이도 같은 이유로 무의존성이다)
 *
 * **에뮬레이터에서 호스트 PC는 10.0.2.2 다.** localhost 는 에뮬레이터 자신이다.
 */
object Net {

    /**
     * 기본 서버 주소.
     *
     * 실기기에서 테스트하려면 설정에서 PC의 LAN IP 로 바꾼다.
     */
    const val DEFAULT_BASE = "http://10.0.2.2:8000"

    /**
     * 무료 호스팅은 15분 놀면 잠들고, 깨는 데 30~60초가 걸린다.
     * 4초 만에 포기하면 데모 중에 늘 데모 데이터로 내려간다.
     */
    private const val CONNECT_MS = 5_000
    private const val READ_MS = 70_000

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    class Unreachable(message: String) : Exception(message)

    private suspend fun request(
        url: String,
        method: String = "GET",
        body: String? = null,
        token: String = "",
    ): String = withContext(Dispatchers.IO) {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = CONNECT_MS
            readTimeout = READ_MS
            setRequestProperty("Accept", "application/json")
            if (token.isNotBlank()) {
                setRequestProperty("Authorization", "Bearer $token")
            }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }
        try {
            if (body != null) {
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()

            if (code !in 200..299) {
                throw Unreachable("HTTP $code: ${text.take(200)}")
            }
            text
        } catch (e: Unreachable) {
            throw e
        } catch (e: Exception) {
            // 연결 실패는 정상 상황이다 - 서버가 없으면 데모 데이터로 돌아간다
            throw Unreachable(e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    suspend fun dashboard(base: String, dogId: String, token: String = ""): Dashboard {
        val raw = request("$base/v1/dogs/$dogId/dashboard", token = token)
        return json.decodeFromString(raw)
    }

    /** 보호자 답변을 올린다. 반환은 서버가 알려주는 조정 결과. */
    suspend fun answer(
        base: String, dogId: String, answers: Map<String, String>, token: String = "",
    ): String {
        val payload = buildString {
            append("""{"answers":{""")
            append(answers.entries.joinToString(",") { (k, v) ->
                """"${k.escape()}":"${v.escape()}""""
            })
            append("}}")
        }
        return request("$base/v1/dogs/$dogId/context", "POST", payload, token)
    }

    suspend fun putWeight(
        base: String, dogId: String, kg: Double, token: String = "",
    ): String = request("$base/v1/dogs/$dogId/weight", "POST", """{"kg":$kg}""", token)

    /**
     * 서버를 깨운다.
     *
     * 잠들어 있던 인스턴스는 첫 요청이 오래 걸린다. 화면을 띄우기 전에
     * 한 번 두드려 두면 그 뒤 요청은 정상 속도로 돌아온다.
     */
    suspend fun wake(base: String): Boolean = try {
        request("$base/health")
        true
    } catch (_: Exception) {
        false
    }

    private fun String.escape(): String =
        replace("\\", "\\\\").replace("\"", "\\\"")
}
