package com.petcare

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import com.petcare.data.MyDog
import com.petcare.data.ProfileStore
import com.petcare.data.Repository
import com.petcare.onboarding.OnboardingScreen
import com.petcare.ui.DashboardScreen
import com.petcare.ui.PetCareTheme
import com.petcare.ui.T
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val repo = Repository(applicationContext)
        val store = ProfileStore(applicationContext)

        setContent {
            PetCareTheme {
                Surface(
                    modifier = Modifier
                        .fillMaxSize()
                        .windowInsetsPadding(WindowInsets.systemBars),
                    color = T.canvas,
                ) {
                    App(repo = repo, store = store)
                }
            }
        }
    }
}

/**
 * 앱의 유일한 분기.
 *
 * 저장된 프로필이 없으면 온보딩, 있으면 대시보드다.
 * 화면이 두 개뿐이라 Navigation 라이브러리를 넣지 않았다.
 * 화면이 더 늘어나면 그때 NavHost로 바꾸는 게 맞다.
 */
@Composable
private fun App(repo: Repository, store: ProfileStore) {
    val scope = rememberCoroutineScope()

    // null = 아직 읽는 중. 여기서 바로 온보딩을 띄우면
    // 이미 등록한 사용자에게 온보딩이 한 번 깜빡인다.
    var loaded by remember { mutableStateOf(false) }
    var dog by remember { mutableStateOf<MyDog?>(null) }
    var editing by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        dog = store.load()
        loaded = true
    }

    if (!loaded) return

    val current = dog
    if (current == null || editing) {
        OnboardingScreen(initial = current) { result ->
            scope.launch {
                store.save(result)
                dog = result
                editing = false
            }
        }
    } else {
        DashboardScreen(
            repo = repo,
            dog = current,
            onEditProfile = { editing = true },
        )
    }
}
