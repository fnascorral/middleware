/*+
 * Copyright 2016 iXsystems, Inc.
 * All rights reserved
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted providing that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
 * IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
 * STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
 * IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 *
 */

#include <string.h>
#include <unistd.h>
#include <sys/param.h>
#include <security/pam_appl.h>
#include <security/pam_modules.h>
#include <security/pam_mod_misc.h>
#include <jansson.h>
#include <dispatcher.h>

#define PASSWD_FILE "/etc/passwd.json"
#define PASSWORD_PROMPT "Password:"

static void flat_load_files();
static json_t *flat_find_user(const char *);
static int call_dispatcher(const char *, json_t *, json_t **);

static json_t *flat_users;

static void
flat_load_files()
{
    json_error_t err;

    flat_users = json_load_file(PASSWD_FILE, 0, &err);
}

static json_t *
flat_find_user(const char *name)
{
    json_t *user;
    json_t *val;
    size_t index;

    if (flat_users == NULL)
        flat_load_files();

    /* Bail out if still null */
    if (flat_users == NULL)
        return (NULL);

    json_array_foreach(flat_users, index, user) {
        val = json_object_get(user, name != NULL ? "username" : "id");
        if (val == NULL)
            continue;

        if (strcmp(json_string_value(val), name) == 0) {
            json_incref(user);
            return (user);
        }
    }

    return (NULL);
}

static int
call_dispatcher(const char *method, json_t *args, json_t **result)
{
    connection_t *conn;
    int err;

    conn = dispatcher_open("unix");
    if (conn == NULL) {
        PAM_LOG("Cannot open unix domain socket connection");
        return (-1);
    }

    if (dispatcher_login_service(conn, "pam-freenas") < 0) {
        PAM_LOG("Cannot log in as pam-freenas");
        dispatcher_close(conn);
        return (-1);
    }

    err = dispatcher_call_sync(conn, method, args, result);
    if (err != RPC_CALL_DONE) {
        PAM_LOG("Cannot call %s: %d", method, err);
        dispatcher_close(conn);
        return (-1);
    }

    json_incref(*result);
    dispatcher_close(conn);
    return (0);
}

PAM_EXTERN int
pam_sm_setcred(struct pam_handle *pamh, int flags, int argc, const char *argv[])
{

    return (PAM_SUCCESS);
}

PAM_EXTERN int
pam_sm_acct_mgmt(struct pam_handle *pamh, int flags, int argc, const char *argv[])
{

    return (PAM_SUCCESS);
}

PAM_EXTERN int
pam_sm_authenticate(struct pam_handle *pamh, int flags, int argc, const char *argv[])
{
    const char *username, *password, *realpw;
    char *result_s;
    json_t *user, *result;
    int err;

    err = pam_get_user(pamh, &username, NULL);
    if (err != PAM_SUCCESS)
        return (err);

    PAM_LOG("Got user: %s", username);

    err = pam_get_authtok(pamh, PAM_AUTHTOK, &password, PASSWORD_PROMPT);
    if (err != PAM_SUCCESS)
        return (err);

    PAM_LOG("Got password");

    if (call_dispatcher("dscached.account.authenticate", json_pack("[ss]", username, password), &result) != 0) {
        PAM_LOG("Cannot call dispatcher, trying local file backend");

        /* Try flat file lookup */
        flat_load_files();

        user = flat_find_user(username);
        if (user == NULL) {
            PAM_LOG("User %s not found", username);
            return (PAM_PERM_DENIED);
        }

        realpw = json_string_value(json_object_get(user, "unixhash"));
        if (realpw == NULL) {
            PAM_LOG("User %s has empty password", username);
            return (PAM_PERM_DENIED);
        }

        if (strcmp(crypt(password, realpw), realpw) == 0)
            return (PAM_SUCCESS);

        return (PAM_AUTH_ERR);
    }

    result_s = json_dumps(result, JSON_ENCODE_ANY);
    PAM_LOG("Result: %s", result_s);
    free(result_s);

    if (json_is_true(result))
        return (PAM_SUCCESS);

    return (PAM_AUTH_ERR);
}

PAM_MODULE_ENTRY("pam_freenas");
