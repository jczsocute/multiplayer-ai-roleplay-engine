/** Wire types shared by the Lobby components and API helpers. */
import type { AuthUser } from "./protocol";

export type TemplateItem = {
  id: string;
  name: string;
  owner_user_id: number;
  owner_username?: string;
  is_public: boolean;
  created_at?: string;
  updated_at?: string;
  role_count?: number;
  role_names?: string[];
  /** Payload metadata: public-facing description (detail only). */
  introduction?: string;
  tags?: string[];
};

export type GameItem = {
  id: string;
  name: string;
  owner_user_id: number;
  source_template_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type RoomItem = {
  code: string;
  game_id: string;
  game_name: string;
  display_name?: string;
  owner_username: string;
  owner_user_id: number;
  role_count: number;
  connected_count: number;
  occupancy?: number;
  max_users?: number;
  has_password: boolean;
};

export type PlatformUser = AuthUser;

export type TemplateEditorData = {
  id: string;
  title: string;
  introduction: string;
  tags: string[];
  world: string;
  ai_guidelines: string;
  characters: Array<{ index: number; character: string; opening: string }>;
};
