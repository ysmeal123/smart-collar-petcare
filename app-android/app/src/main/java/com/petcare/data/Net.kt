package com.petcare.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
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

    /**
     * 선택지 답변. 질문의 key 를 그대로 보낸다.
     *
     * 자유 텍스트(note)와 경로를 나누는 이유: 칩은 이미 자기 key 를 안다.
     * 그걸 문장으로 바꿔 보내면 서버가 다시 추출해야 하고, "보여요" 같은
     * 짧은 답은 추출에 실패한다. 아는 것을 버리고 추론하게 만들면 안 된다.
     */
    suspend fun answerQuestion(
        base: String, dogId: String, key: String, value: String, token: String = "",
    ): NoteResult {
        val payload = """{"answers":{"${key.escape()}":"${value.escape()}"}}"""
        val raw = request("$base/v1/dogs/$dogId/context", "POST", payload, token)
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

    suspend fun chat(base: String, dogId: String, token: String = ""): ChatLog {
        val raw = request("$base/v1/dogs/$dogId/chat", token = token)
        return json.decodeFromString(raw)
    }

    /** 자유 텍스트 특이사항. 서버가 해석 결과와 갱신된 대화록을 돌려준다. */
    suspend fun note(
        base: String, dogId: String, text: String, token: String = "",
    ): NoteResult {
        val payload = """{"text":"${text.escape()}"}"""
        val raw = request("$base/v1/dogs/$dogId/note", "POST", payload, token)
        return json.decodeFromString(raw)
    }

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

    // -----------------------------------------------------------------------
    // 밥통 — 서버를 거쳐 조작한다
    //
    // 앱이 ESP32 의 IP 로 직접 치면 같은 핫스팟에 있을 때만 된다.
    // 서버에 명령을 쌓으면 밥통이 60초마다 가져가므로 집 밖에서도 되고,
    // 앱이 꺼져 있어도 급여는 나간다.
    // -----------------------------------------------------------------------

    @Serializable
    data class FeederMinute(
        @SerialName("rest_s") val restS: Int = 0,
        @SerialName("walk_s") val walkS: Int = 0,
        @SerialName("run_s") val runS: Int = 0,
        @SerialName("shake_n") val shakeN: Int = 0,
    )

    /** 밥통이 마지막으로 보고한 자기 상태. */
    @Serializable
    data class FeederDeviceState(
        val busy: Boolean = false,
        @SerialName("last_job") val lastJob: String = "",
        @SerialName("collar_connected") val collarConnected: Boolean = false,
        /** rest / walk / run / body_shake. 빈 문자열이면 아직 판정이 없다. */
        @SerialName("collar_class") val collarClass: String = "",
        @SerialName("collar_conf") val collarConf: Double = 0.0,
        val minute: FeederMinute = FeederMinute(),
    )

    @Serializable
    data class FeederAck(
        val id: String = "",
        val state: String = "",
        val result: String = "",
    )

    /**
     * 앱의 밥통 화면이 읽는 값.
     *
     * `online` 이 거짓이면 밥통이 보고를 끊은 것이다. 그래도 버튼은 눌린다 —
     * 명령은 쌓이고, 10분 안에 깨면 실행된다.
     */
    @Serializable
    data class FeederView(
        val online: Boolean = false,
        @SerialName("age_s") val ageS: Int? = null,
        val state: FeederDeviceState? = null,
        val pending: Int = 0,
        @SerialName("last_ack") val lastAck: FeederAck? = null,
    )

    suspend fun feeder(
        base: String, dogId: String, token: String = "",
    ): FeederView = json.decodeFromString(
        request("$base/v1/dogs/$dogId/feeder", token = token)
    )

    /**
     * 수동 급여를 서버에 쌓는다.
     *
     * [pellets] 는 슬롯 -> 알 수. 사료는 슬롯이 아니라 [foodG] 로 따로 보낸다 —
     * 알은 세는 것이고 사료는 재는 것이라 단위가 다르다.
     *
     * [requestId] 는 버튼 한 번에 하나다. 응답을 못 받아 다시 보낼 때
     * **같은 값을 써야** 두 번 나가지 않는다.
     */
    suspend fun dispense(
        base: String,
        dogId: String,
        pellets: Map<Int, Int>,
        foodG: Double = 0.0,
        requestId: String = "",
        token: String = "",
    ): String {
        val body = buildString {
            append("""{"pellets":{""")
            append(pellets.entries.joinToString(",") { (slot, n) -> """"$slot":$n""" })
            append("""},"food_g":$foodG""")
            append(""","request_id":"${requestId.escape()}"}""")
        }
        return request("$base/v1/dogs/$dogId/dispense", "POST", body, token)
    }

    /**
     * 아직 안 가져간 명령을 취소한다.
     *
     * **진행 중인 배출은 못 멈춘다.** 밥통이 이미 가져간 명령에는 손이 닿지
     * 않는다. 그건 같은 핫스팟에서 밥통에 직접 쳐야 한다.
     */
    suspend fun cancelDispense(
        base: String, dogId: String, token: String = "",
    ): String = request("$base/v1/dogs/$dogId/dispense/cancel", "POST", "{}", token)

    private fun String.escape(): String =
        replace("\\", "\\\\").replace("\"", "\\\"")
}
