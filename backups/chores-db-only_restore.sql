-- Current Database: `chores-db`
--

/*!40000 DROP DATABASE IF EXISTS `chores-db`*/;

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `chores-db` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `chores-db`;

--
-- Table structure for table `activities`
--

DROP TABLE IF EXISTS `activities`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `activities` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `activity_type` varchar(50) NOT NULL,
  `description` text NOT NULL,
  `target_user_id` int DEFAULT NULL,
  `activity_data` json DEFAULT NULL COMMENT 'Activity-specific data like chore_id, amount, etc.',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `target_user_id` (`target_user_id`),
  KEY `ix_activities_activity_type` (`activity_type`),
  KEY `ix_activities_created_at` (`created_at`),
  KEY `ix_activities_id` (`id`),
  KEY `ix_activities_user_id` (`user_id`),
  CONSTRAINT `activities_ibfk_1` FOREIGN KEY (`target_user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `activities_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `activities`
--

LOCK TABLES `activities` WRITE;
/*!40000 ALTER TABLE `activities` DISABLE KEYS */;
/*!40000 ALTER TABLE `activities` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `alembic_version`
--

DROP TABLE IF EXISTS `alembic_version`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `alembic_version`
--

LOCK TABLES `alembic_version` WRITE;
/*!40000 ALTER TABLE `alembic_version` DISABLE KEYS */;
INSERT INTO `alembic_version` VALUES ('0582f39dfdd4');
/*!40000 ALTER TABLE `alembic_version` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `chore_assignments`
--

DROP TABLE IF EXISTS `chore_assignments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `chore_assignments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `chore_id` int NOT NULL,
  `assignee_id` int NOT NULL,
  `is_completed` tinyint(1) NOT NULL DEFAULT '0',
  `is_approved` tinyint(1) NOT NULL DEFAULT '0',
  `completion_date` datetime DEFAULT NULL,
  `approval_date` datetime DEFAULT NULL,
  `approval_reward` float DEFAULT NULL,
  `rejection_reason` text,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_chore_assignee` (`chore_id`,`assignee_id`),
  KEY `idx_assignments_chore` (`chore_id`),
  KEY `idx_assignments_assignee` (`assignee_id`),
  KEY `idx_assignments_completed` (`is_completed`),
  CONSTRAINT `chore_assignments_ibfk_1` FOREIGN KEY (`chore_id`) REFERENCES `chores` (`id`) ON DELETE CASCADE,
  CONSTRAINT `chore_assignments_ibfk_2` FOREIGN KEY (`assignee_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `chore_assignments`
--

LOCK TABLES `chore_assignments` WRITE;
/*!40000 ALTER TABLE `chore_assignments` DISABLE KEYS */;
INSERT INTO `chore_assignments` VALUES (1,1,7,0,0,NULL,NULL,NULL,NULL,'2025-10-28 18:21:31','2025-10-28 18:21:31'),(2,2,8,0,0,NULL,NULL,NULL,NULL,'2025-10-28 18:21:31','2025-10-28 18:21:31');
/*!40000 ALTER TABLE `chore_assignments` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `chores`
--

DROP TABLE IF EXISTS `chores`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `chores` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `reward` float NOT NULL,
  `min_reward` float DEFAULT NULL,
  `max_reward` float DEFAULT NULL,
  `is_range_reward` tinyint(1) NOT NULL,
  `cooldown_days` int NOT NULL,
  `is_recurring` tinyint(1) NOT NULL,
  `frequency` varchar(50) DEFAULT NULL,
  `is_disabled` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `creator_id` int NOT NULL,
  `approval_reward` float DEFAULT NULL,
  `rejection_reason` text,
  `assignment_mode` varchar(20) NOT NULL DEFAULT 'single',
  PRIMARY KEY (`id`),
  KEY `ix_chores_id` (`id`),
  KEY `ix_chores_title` (`title`),
  KEY `idx_chore_creator_id` (`creator_id`),
  KEY `idx_chore_status` (`is_disabled`),
  KEY `idx_chore_created_at` (`created_at`),
  KEY `idx_chores_mode` (`assignment_mode`),
  CONSTRAINT `chores_ibfk_2` FOREIGN KEY (`creator_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `chores`
--

LOCK TABLES `chores` WRITE;
/*!40000 ALTER TABLE `chores` DISABLE KEYS */;
INSERT INTO `chores` VALUES (1,'[TEST] Health Check Chore - Fixed Reward','Test chore for monitoring validation with fixed reward',5,NULL,NULL,0,0,0,NULL,0,'2025-10-28 18:21:31','2025-10-28 18:21:31',6,NULL,NULL,'single'),(2,'[TEST] Health Check Chore - Range Reward','Test chore for monitoring validation with range reward',5,3,10,1,0,0,NULL,0,'2025-10-28 18:21:31','2025-10-28 18:21:31',6,NULL,NULL,'single');
/*!40000 ALTER TABLE `chores` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `families`
--

DROP TABLE IF EXISTS `families`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `families` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) DEFAULT NULL,
  `invite_code` varchar(8) NOT NULL,
  `invite_code_expires_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_families_invite_code` (`invite_code`),
  KEY `ix_families_id` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `families`
--

LOCK TABLES `families` WRITE;
/*!40000 ALTER TABLE `families` DISABLE KEYS */;
INSERT INTO `families` VALUES (1,'Sela','MFS90WZE',NULL,'2025-09-20 17:14:59','2025-09-20 17:14:59'),(2,'Monitoring & Health Checks','OHF723YR','2026-10-28 18:21:19','2025-10-28 18:21:18','2025-10-28 18:21:18');
/*!40000 ALTER TABLE `families` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `reward_adjustments`
--

DROP TABLE IF EXISTS `reward_adjustments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `reward_adjustments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `child_id` int NOT NULL,
  `parent_id` int NOT NULL,
  `amount` decimal(10,2) NOT NULL,
  `reason` varchar(500) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_child_adjustments` (`child_id`),
  KEY `idx_parent_adjustments` (`parent_id`),
  CONSTRAINT `reward_adjustments_ibfk_1` FOREIGN KEY (`child_id`) REFERENCES `users` (`id`),
  CONSTRAINT `reward_adjustments_ibfk_2` FOREIGN KEY (`parent_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `reward_adjustments`
--

LOCK TABLES `reward_adjustments` WRITE;
/*!40000 ALTER TABLE `reward_adjustments` DISABLE KEYS */;
INSERT INTO `reward_adjustments` VALUES (1,4,2,5.00,'Bonus for extra help','2025-10-12 23:34:37'),(2,3,2,48.00,'initial bonus','2025-10-12 23:34:55');
/*!40000 ALTER TABLE `reward_adjustments` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(255) DEFAULT NULL,
  `username` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `hashed_password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `is_parent` tinyint(1) NOT NULL,
  `parent_id` int DEFAULT NULL,
  `family_id` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_username` (`username`),
  UNIQUE KEY `ix_users_email` (`email`),
  KEY `ix_users_id` (`id`),
  KEY `idx_user_parent_id` (`parent_id`),
  KEY `ix_users_family_id` (`family_id`),
  CONSTRAINT `users_ibfk_1` FOREIGN KEY (`parent_id`) REFERENCES `users` (`id`),
  CONSTRAINT `users_ibfk_2` FOREIGN KEY (`family_id`) REFERENCES `families` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` VALUES (1,'test@example.com','testuser123','$2b$12$7xPZb8pa12sfMRidE9uTjOnJYp9XKwQgYK./3jvz9OZfqx1ZNL/c.',1,1,NULL,NULL,'2025-09-20 01:21:28','2025-09-20 01:21:28'),(2,'arigsela@gmail.com','asela','$2b$12$E8OqAjDpAo.GQ1grrKayseTmOadYJfQOU2XdbcxJFjMsy4aiwjo4G',1,1,NULL,1,'2025-09-20 16:33:15','2025-10-12 23:17:37'),(3,'makoto.olivarria@gmail.com','Makoto','$2b$12$gjemo0jwLy.98T2wtbsnreRw/awMpQ/ofwDwmJ32y8jFD9ISC/cTi',1,0,2,1,'2025-09-20 16:34:48','2025-10-12 23:29:52'),(4,'eleanor.sela@gmail.com','Eli','$2b$12$tYvisWkn1rswYF0Jl5wwbuJrvtFFNeCWJkOCgeRtmxjhtQupY.Vb2',1,0,2,1,'2025-09-20 16:35:21','2025-10-12 23:29:52'),(5,'maruzhan@hotmail.com','diana','$2b$12$szC/uZDRPG9I..oNPDYxh.T3IY9wPTazw87usxIn82Wm.qPblUPO.',1,1,NULL,1,'2025-09-20 17:15:35','2025-09-20 17:15:57'),(6,'monitoring@healthcheck.local','monitoring_agent','$2b$12$jJIZrj5ZgnkGdUtZZiyjk.GReqZRfNxyDwVl1PyrliPBzNqQpgZXS',1,1,NULL,2,'2025-10-28 18:21:23','2025-10-28 18:21:23'),(7,'test_child_1@healthcheck.local','test_child_monitor_1','$2b$12$mwzWOWsTDDN87Dh0Kt1uyOzwxCOEyWR.i5RTd2kK3ISGnWG6jwP.y',1,0,6,2,'2025-10-28 18:21:30','2025-10-28 18:21:30'),(8,'test_child_2@healthcheck.local','test_child_monitor_2','$2b$12$/q5wMGAoXzyg35zFYfl3eeAUUJmKGDRIR154Vb7qRSH/f9LHbG4MC',1,0,6,2,'2025-10-28 18:21:30','2025-10-28 18:21:30');
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping events for database 'chores-db'
--

--
-- Dumping routines for database 'chores-db'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;
/*!50606 SET GLOBAL INNODB_STATS_AUTO_RECALC=@OLD_INNODB_STATS_AUTO_RECALC */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2025-10-29  2:00:36
