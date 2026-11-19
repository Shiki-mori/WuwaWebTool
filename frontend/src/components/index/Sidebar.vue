<script setup lang="ts">
import UserCard from './UserCard.vue'
import DataOverview from './DataOverview.vue'
import SignInCard from './SignInCard.vue'
import AutoSignCard from './AutoSignCard.vue'
</script>

<template>
  <div class="page-sidebar">
    <UserCard />
    <SignInCard />
    <AutoSignCard />
    <DataOverview />
  </div>
</template>
